#!/usr/bin/env python
# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: : 2017-2020 The PyPSA-Eur Authors, 2021 PyPSA-Africa authors
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calculates for each network node the
(i) installable capacity (based on land-use), (ii) the available generation time
series (based on weather data), and (iii) the average distance from the node for
onshore wind, AC-connected offshore wind, DC-connected offshore wind and solar
PV generators. For hydro generators, it calculates the expected inflows.
In addition for offshore wind it calculates the fraction of the grid connection
which is under water.

Relevant settings
-----------------

.. code:: yaml

    snapshots:

    atlite:
        nprocesses:

    renewable:
        {technology}:
            cutout:
            copernicus:
                grid_codes:
                distance:
                distance_grid_codes:
            natura:
            max_depth:
            max_shore_distance:
            min_shore_distance:
            capacity_per_sqkm:
            correction_factor:
            potential:
            min_p_max_pu:
            clip_p_max_pu:
            resource:
            clip_min_inflow:

.. seealso::
    Documentation of the configuration file ``config.yaml`` at
    :ref:`snapshots_cf`, :ref:`atlite_cf`, :ref:`renewable_cf`

Inputs
------

- ``data/copernicus/PROBAV_LC100_global_v3.0.1_2019-nrt_Discrete-Classification-map_EPSG-4326.tif``: `Copernicus Land Service <https://land.copernicus.eu/global/products/lc>`_ inventory on 23 land use classes (e.g. forests, arable land, industrial, urban areas) based on UN-FAO classification. See `Table 4 in the PUM <https://land.copernicus.eu/global/sites/cgls.vito.be/files/products/CGLOPS1_PUM_LC100m-V3_I3.4.pdf>`_ for a list of all classes.

    .. image:: ../img/copernicus.png
        :scale: 33 %

- ``data/gebco/GEBCO_2021_TID.nc``: A `bathymetric <https://en.wikipedia.org/wiki/Bathymetry>`_ data set with a global terrain model for ocean and land at 15 arc-second intervals by the `General Bathymetric Chart of the Oceans (GEBCO) <https://www.gebco.net/data_and_products/gridded_bathymetry_data/>`_.

    .. image:: ../img/gebco_2021_grid_image.jpg
        :scale: 50 %

    **Source:** `GEBCO <https://www.gebco.net/data_and_products/images/gebco_2019_grid_image.jpg>`_

- ``resources/natura.tiff``: confer :ref:`natura`
- ``resources/offshore_shapes.geojson``: confer :ref:`shapes`
- ``resources/.geojson``: (if not offshore wind), confer :ref:`busregions`
- ``resources/regions_offshore.geojson``: (if offshore wind), :ref:`busregions`
- ``"cutouts/" + config["renewable"][{technology}]['cutout']``: :ref:`cutout`
- ``networks/base.nc``: :ref:`base`

Outputs
-------

- ``resources/profile_{technology}.nc``, except hydro technology, with the following structure

    ===================  ==========  =========================================================
    Field                Dimensions  Description
    ===================  ==========  =========================================================
    profile              bus, time   the per unit hourly availability factors for each node
    -------------------  ----------  ---------------------------------------------------------
    weight               bus         sum of the layout weighting for each node
    -------------------  ----------  ---------------------------------------------------------
    p_nom_max            bus         maximal installable capacity at the node (in MW)
    -------------------  ----------  ---------------------------------------------------------
    potential            y, x        layout of generator units at cutout grid cells inside the
                                     Voronoi cell (maximal installable capacity at each grid
                                     cell multiplied by capacity factor)
    -------------------  ----------  ---------------------------------------------------------
    average_distance     bus         average distance of units in the Voronoi cell to the
                                     grid node (in km)
    -------------------  ----------  ---------------------------------------------------------
    underwater_fraction  bus         fraction of the average connection distance which is
                                     under water (only for offshore)
    ===================  ==========  =========================================================

- ``resources/profile_hydro.nc`` for the hydro technology
    ===================  ================  ========================================================
    Field                Dimensions        Description
    ===================  ================  ========================================================
    inflow               plant, time       Inflow to the state of charge (in MW),
                                           e.g. due to river inflow in hydro reservoir.
    ===================  ================  ========================================================

    - **profile**

    .. image:: ../img/profile_ts.png
        :scale: 33 %
        :align: center

    - **p_nom_max**

    .. image:: ../img/p_nom_max_hist.png
        :scale: 33 %
        :align: center

    - **potential**

    .. image:: ../img/potential_heatmap.png
        :scale: 33 %
        :align: center

    - **average_distance**

    .. image:: ../img/distance_hist.png
        :scale: 33 %
        :align: center

    - **underwater_fraction**

    .. image:: ../img/underwater_hist.png
        :scale: 33 %
        :align: center

Description
-----------

This script leverages on atlite function to derivate hourly time series for an entire year
for solar, wind (onshore and offshore), and hydro data.

This script functions at two main spatial resolutions: the resolution of the
network nodes and their `Voronoi cells
<https://en.wikipedia.org/wiki/Voronoi_diagram>`_, and the resolution of the
cutout grid cells for the weather data. Typically the weather data grid is
finer than the network nodes, so we have to work out the distribution of
generators across the grid cells within each Voronoi cell. This is done by
taking account of a combination of the available land at each grid cell and the
capacity factor there.

This uses the Copernicus land use data,
Natura2000 nature reserves and GEBCO bathymetry data.

.. image:: ../img/eligibility.png
    :scale: 50 %
    :align: center

To compute the layout of generators in each node's Voronoi cell, the
installable potential in each grid cell is multiplied with the capacity factor
at each grid cell. This is done since we assume more generators are installed
at cells with a higher capacity factor.

.. image:: ../img/offwinddc-gridcell.png
    :scale: 50 %
    :align: center

.. image:: ../img/offwindac-gridcell.png
    :scale: 50 %
    :align: center

.. image:: ../img/onwind-gridcell.png
    :scale: 50 %
    :align: center

.. image:: ../img/solar-gridcell.png
    :scale: 50 %
    :align: center

This layout is then used to compute the generation availability time series
from the weather data cutout from ``atlite``.

Two methods are available to compute the maximal installable potential for the
node (`p_nom_max`): ``simple`` and ``conservative``:

- ``simple`` adds up the installable potentials of the individual grid cells.
  If the model comes close to this limit, then the time series may slightly
  overestimate production since it is assumed the geographical distribution is
  proportional to capacity factor.

- ``conservative`` assertains the nodal limit by increasing capacities
  proportional to the layout until the limit of an individual grid cell is
  reached.

"""
import functools
import logging
import os
import time

import atlite
import country_converter as coco
import geopandas as gpd
import numpy as np
import pandas as pd
import progressbar as pgb
import xarray as xr
from _helpers import configure_logging, read_csv_nafix, sets_path_to_root
from add_electricity import load_powerplants
from pypsa.geo import haversine
from shapely.geometry import LineString

cc = coco.CountryConverter()

logger = logging.getLogger(__name__)

COPERNICUS_CRS = "EPSG:4326"
GEBCO_CRS = "EPSG:4326"


def get_eia_annual_hydro_generation(fn, countries):

    # in billion kWh/a = TWh/a
    df = pd.read_csv(fn, skiprows=1, index_col=1, na_values=[" ", "--"]).iloc[1:, 1:]
    df.index = df.index.str.strip()

    df.loc["Germany"] = df.filter(like="Germany", axis=0).sum()
    df.loc["Serbia"] += df.loc["Kosovo"]
    df = df.loc[~df.index.str.contains("Former")]
    df.drop(["World", "Germany, West", "Germany, East"], inplace=True)

    df.index = cc.convert(df.index, to="iso2")
    df.index.name = "countries"

    df = df.T[countries] * 1e6  # in MWh/a

    return df


def get_hydro_capacity_annual_hydro_generation(
    fn, countries, year_start=2000, year_end=2020
):
    hydro_stats = (
        pd.read_csv(
            fn,
            comment="#",
            na_values=["-"],
            index_col=0,
        )
        .rename({"Country": "countries"}, axis=1)
        .set_index("countries")
    )
    hydro_prod_by_country = (
        hydro_stats[hydro_stats.index.isin(countries)][
            ["InflowHourlyAvg[GWh]"]
        ].transpose()
        * 1e3
        * 8760
    )  # change unit to MWh/y

    range_years = range(year_start, year_end + 1)

    normalize_using_yearly = pd.concat(
        [hydro_prod_by_country] * len(range_years), ignore_index=True
    )
    normalize_using_yearly.index = pd.Index(range_years)

    return normalize_using_yearly


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        snakemake = mock_snakemake("build_renewable_profiles", technology="hydro")
        sets_path_to_root("pypsa-africa")
    configure_logging(snakemake)

    pgb.streams.wrap_stderr()
    countries = snakemake.config["countries"]
    paths = snakemake.input
    nprocesses = snakemake.config["atlite"].get("nprocesses")
    noprogress = not snakemake.config["atlite"].get("show_progress", False)
    config = snakemake.config["renewable"][snakemake.wildcards.technology]
    resource = config["resource"]
    correction_factor = config.get("correction_factor", 1.0)
    p_nom_max_meth = config.get("potential", "conservative")

    # crs
    geo_crs = snakemake.config["crs"]["geo_crs"]
    area_crs = snakemake.config["crs"]["area_crs"]

    if isinstance(config.get("copernicus", {}), list):
        config["copernicus"] = {"grid_codes": config["copernicus"]}

    if correction_factor != 1.0:
        logger.info(f"correction_factor is set as {correction_factor}")

    cutout = atlite.Cutout(paths["cutout"])
    regions = gpd.read_file(paths.regions).set_index("name").rename_axis("bus")

    if snakemake.config["cluster_options"]["alternative_clustering"]:
        regions = gpd.GeoDataFrame(
            regions.reset_index()
            .groupby("shape_id")
            .agg(
                {
                    "x": "mean",
                    "y": "mean",
                    "country": "first",
                    "geometry": "first",
                    "bus": "first",
                }
            )
            .set_index("bus"),
            crs=regions.crs,
        )

    buses = regions.index

    func = getattr(cutout, resource.pop("method"))
    resource["dask_kwargs"] = {"num_workers": nprocesses}

    # filter plants for hydro
    if snakemake.wildcards.technology.startswith("hydro"):
        country_shapes = gpd.read_file(paths.country_shapes)
        hydrobasins = gpd.read_file(resource["hydrobasins"])
        ppls = load_powerplants(snakemake.input.powerplants)

        hydro_ppls = ppls[ppls.carrier == "hydro"]

        # commented out as rivers may span across multiple countries
        # hydrobasins = hydrobasins[
        #     [
        #         any(country_shapes.geometry.intersects(geom))
        #         for geom in hydrobasins.geometry
        #     ]
        # ]  # exclude hydrobasins shapes that do not intersect the countries of interest

        # select busbar whose location (p) belongs to at least one hydrobasin geometry
        # if extendable option is true, all buses are included
        # otherwise only where hydro powerplants are available are considered
        busbus_to_consider = [
            (config.get("extendable", False) | (bus_id in hydro_ppls.bus.values))
            & any(hydrobasins.geometry.intersects(p))
            for (p, bus_id) in zip(
                gpd.points_from_xy(regions.x, regions.y, crs=regions.crs),
                regions.index,
            )
        ]

        resource["plants"] = regions.rename(
            columns={"x": "lon", "y": "lat", "country": "countries"}
        ).loc[busbus_to_consider, ["lon", "lat", "countries"]]

        resource["plants"]["installed_hydro"] = [
            True if (bus_id in hydro_ppls.bus.values) else False
            for bus_id in resource["plants"].index
        ]

        # check if normalization field belongs to the settings and it is not false
        if ("normalization" in resource) & (type(resource["normalization"]) == str):

            normalization = resource.pop("normalization")

            if normalization == "hydro_capacities":
                path_hydro_capacities = snakemake.input.hydro_capacities
                normalize_using_yearly = get_hydro_capacity_annual_hydro_generation(
                    path_hydro_capacities, countries
                ) * config.get("normalization_multiplier", 1.0)

            elif normalization == "eia":
                path_eia_stats = snakemake.input.eia_hydro_generation
                normalize_using_yearly = get_eia_annual_hydro_generation(
                    path_eia_stats, countries
                ) * config.get("normalization_multiplier", 1.0)

            resource["normalize_using_yearly"] = normalize_using_yearly
            logger.info(f"Hydro normalization mode {normalization}")
        else:
            logger.info("No hydro normalization")

            if "normalization" in resource:
                resource.pop("normalization")

        # check if there are hydro powerplants
        if resource["plants"].empty:
            # when no powerplants are available save an empty file
            xr.DataArray(
                dims=["plant", "time"], coords={"plant": []}, name="inflow"
            ).to_netcdf(snakemake.output.profile)
        else:
            # otherwise perform the calculations
            inflow = correction_factor * func(capacity_factor=True, **resource)

            if "clip_min_inflow" in config:
                inflow = inflow.where(inflow > config["clip_min_inflow"], 0)

            inflow.rename("inflow").to_netcdf(snakemake.output.profile)
    else:

        capacity_per_sqkm = config["capacity_per_sqkm"]

        excluder = atlite.ExclusionContainer(crs=area_crs, res=100)

        if "natura" in config and config["natura"]:
            excluder.add_raster(paths.natura, nodata=0, allow_no_overlap=True)

        if "copernicus" in config and config["copernicus"]:
            copernicus = config["copernicus"]
            excluder.add_raster(
                paths.copernicus,
                codes=copernicus["grid_codes"],
                invert=True,
                crs=COPERNICUS_CRS,
            )
            if "distance" in copernicus and config["copernicus"]["distance"] > 0:
                excluder.add_raster(
                    paths.copernicus,
                    codes=copernicus["distance_grid_codes"],
                    buffer=copernicus["distance"],
                    crs=COPERNICUS_CRS,
                )

        if "max_depth" in config:
            # lambda not supported for atlite + multiprocessing
            # use named function np.greater with partially frozen argument instead
            # and exclude areas where: -max_depth > grid cell depth
            func_depth = functools.partial(np.greater, -config["max_depth"])
            excluder.add_raster(
                paths.gebco, codes=func_depth, crs=GEBCO_CRS, nodata=-1000
            )

        if "min_shore_distance" in config:
            buffer = config["min_shore_distance"]
            excluder.add_geometry(paths.country_shapes, buffer=buffer)

        if "max_shore_distance" in config:
            buffer = config["max_shore_distance"]
            excluder.add_geometry(paths.country_shapes, buffer=buffer, invert=True)

        kwargs = dict(nprocesses=nprocesses, disable_progressbar=noprogress)
        if noprogress:
            logger.info("Calculate landuse availabilities...")
            start = time.time()
            availability = cutout.availabilitymatrix(regions, excluder, **kwargs)
            duration = time.time() - start
            logger.info(f"Completed availability calculation ({duration:2.2f}s)")
        else:
            availability = cutout.availabilitymatrix(regions, excluder, **kwargs)

        area = cutout.grid.to_crs(area_crs).area / 1e6
        area = xr.DataArray(
            area.values.reshape(cutout.shape), [cutout.coords["y"], cutout.coords["x"]]
        )

        potential = capacity_per_sqkm * availability.sum("bus") * area

        capacity_factor = correction_factor * func(capacity_factor=True, **resource)
        layout = capacity_factor * area * capacity_per_sqkm

        profile, capacities = func(
            matrix=availability.stack(spatial=["y", "x"]),
            layout=layout,
            index=buses,
            per_unit=True,
            return_capacity=True,
            **resource,
        )

        logger.info(f"Calculating maximal capacity per bus (method '{p_nom_max_meth}')")
        if p_nom_max_meth == "simple":
            p_nom_max = capacity_per_sqkm * availability @ area
        elif p_nom_max_meth == "conservative":
            max_cap_factor = capacity_factor.where(availability != 0).max(["x", "y"])
            p_nom_max = capacities / max_cap_factor
        else:
            raise AssertionError(
                'Config key `potential` should be one of "simple" '
                f'(default) or "conservative", not "{p_nom_max_meth}"'
            )

        logger.info("Calculate average distances.")
        layoutmatrix = (layout * availability).stack(spatial=["y", "x"])

        coords = cutout.grid[["x", "y"]]
        bus_coords = regions[["x", "y"]]

        average_distance = []
        centre_of_mass = []
        for bus in buses:
            row = layoutmatrix.sel(bus=bus).data
            nz_b = row != 0
            row = row[nz_b]
            co = coords[nz_b]
            distances = haversine(bus_coords.loc[bus], co)
            average_distance.append((distances * (row / row.sum())).sum())
            centre_of_mass.append(co.values.T @ (row / row.sum()))

        average_distance = xr.DataArray(average_distance, [buses])
        centre_of_mass = xr.DataArray(centre_of_mass, [buses, ("spatial", ["x", "y"])])

        ds = xr.merge(
            [
                (correction_factor * profile).rename("profile"),
                capacities.rename("weight"),
                p_nom_max.rename("p_nom_max"),
                potential.rename("potential"),
                average_distance.rename("average_distance"),
            ]
        )

        if snakemake.wildcards.technology.startswith("offwind"):
            logger.info("Calculate underwater fraction of connections.")
            offshore_shape = gpd.read_file(paths["offshore_shapes"]).unary_union
            underwater_fraction = []
            for bus in buses:
                p = centre_of_mass.sel(bus=bus).data
                line = LineString([p, regions.loc[bus, ["x", "y"]]])
                frac = line.intersection(offshore_shape).length / line.length
                underwater_fraction.append(frac)

            ds["underwater_fraction"] = xr.DataArray(underwater_fraction, [buses])

        # select only buses with some capacity and minimal capacity factor
        ds = ds.sel(
            bus=(
                (ds["profile"].mean("time") > config.get("min_p_max_pu", 0.0))
                & (ds["p_nom_max"] > config.get("min_p_nom_max", 0.0))
            )
        )

        if "clip_p_max_pu" in config:
            min_p_max_pu = config["clip_p_max_pu"]
            ds["profile"] = ds["profile"].where(ds["profile"] >= min_p_max_pu, 0)

        ds.to_netcdf(snakemake.output.profile)

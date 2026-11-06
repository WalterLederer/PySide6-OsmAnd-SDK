"""Tile collection and viewport-centred request prioritisation."""

from __future__ import annotations

import math

from maps.tile_backend import TilePayload

from .tile_manager import TileManager
from .viewport import ViewState


def collect_tiles(
    view_state: ViewState,
    tile_manager: TileManager,
) -> tuple[
    list[tuple[tuple[int, int, int], TilePayload, float, float, int, int]],
    list[tuple[tuple[float, ...], tuple[int, int, int]]],
]:
    """Gather visible tiles and return missing requests in centre-first order."""

    start_tile_x = math.floor(view_state.view_top_left_x / view_state.scaled_tile_size)
    start_tile_y = math.floor(view_state.view_top_left_y / view_state.scaled_tile_size)
    end_tile_x = math.ceil(
        (view_state.view_top_left_x + view_state.width) / view_state.scaled_tile_size
    )
    end_tile_y = math.ceil(
        (view_state.view_top_left_y + view_state.height) / view_state.scaled_tile_size
    )

    # Include one tile around the viewport to hide fractional-zoom seams.
    start_tile_x -= 1
    start_tile_y -= 1
    end_tile_x += 1
    end_tile_y += 1

    tiles_to_draw: list[
        tuple[tuple[int, int, int], TilePayload, float, float, int, int]
    ] = []
    tiles_to_request: list[
        tuple[tuple[float, ...], tuple[int, int, int]]
    ] = []

    viewport_center_x = view_state.width / 2.0
    viewport_center_y = view_state.height / 2.0

    for tile_y in range(start_tile_y, end_tile_y):
        if tile_y < 0 or tile_y >= view_state.tiles_across:
            continue
        for tile_x in range(start_tile_x, end_tile_x):
            wrapped_x = tile_x % view_state.tiles_across
            tile_scheme = tile_manager.metadata.tile_scheme
            resolved_y = (
                (view_state.tiles_across - 1) - tile_y
                if tile_scheme == "tms"
                else tile_y
            )
            tile_key = (view_state.fetch_zoom, wrapped_x, resolved_y)

            tile_origin_x = (
                tile_x * view_state.scaled_tile_size - view_state.view_top_left_x
            )
            tile_origin_y = (
                tile_y * view_state.scaled_tile_size - view_state.view_top_left_y
            )

            tile_data = tile_manager.get_tile(tile_key)
            if tile_data is None:
                if not tile_manager.is_tile_missing(tile_key):
                    tile_center_x = tile_origin_x + view_state.scaled_tile_size / 2.0
                    tile_center_y = tile_origin_y + view_state.scaled_tile_size / 2.0
                    dx = tile_center_x - viewport_center_x
                    dy = tile_center_y - viewport_center_y
                    intersects_viewport = (
                        tile_origin_x < view_state.width
                        and tile_origin_x + view_state.scaled_tile_size > 0
                        and tile_origin_y < view_state.height
                        and tile_origin_y + view_state.scaled_tile_size > 0
                    )
                    # Visible tiles always outrank seam/preload tiles. Within
                    # each group, render the tile nearest the viewport centre.
                    priority = (
                        0.0 if intersects_viewport else 1.0,
                        dx * dx + dy * dy,
                        float(tile_y),
                        float(tile_x),
                    )
                    tiles_to_request.append((priority, tile_key))
                continue

            tiles_to_draw.append(
                (tile_key, tile_data, tile_origin_x, tile_origin_y, wrapped_x, tile_y)
            )

    return tiles_to_draw, tiles_to_request


def request_tiles(
    tiles_to_request: list[
        tuple[tuple[float, ...], tuple[int, int, int]]
    ],
    tile_manager: TileManager,
) -> None:
    """Submit missing tiles with their centre-first priority."""

    for priority, tile_key in sorted(tiles_to_request, key=lambda item: item[0]):
        tile_manager.ensure_tile(tile_key, priority=priority)


__all__ = ["collect_tiles", "request_tiles"]

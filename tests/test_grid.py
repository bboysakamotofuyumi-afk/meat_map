from meatmap import grid


def test_generate_grid_small_box():
    bbox = (35.0, 139.0, 35.01, 139.02)
    points = grid.generate_grid(bbox=bbox, spacing_m=1000, radius_m=900)
    assert points
    assert all(point.radius_m == 900 for point in points)
    assert min(point.lat for point in points) >= bbox[0]
    assert max(point.lng for point in points) <= bbox[3]


def test_chunked_grid():
    points = grid.generate_grid(bbox=(35.0, 139.0, 35.0, 139.01), spacing_m=500, radius_m=500)
    chunks = list(grid.chunked_grid(points, 3))
    assert sum(len(chunk) for chunk in chunks) == len(points)

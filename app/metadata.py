# /app/metadata.py
import sys, json, rasterio

if len(sys.argv) != 2:
    print(json.dumps({"error": "Usage: metadata.py <path>"}))
    sys.exit(1)

path = sys.argv[1]

try:
    with rasterio.open(path) as src:
        bounds = src.bounds
        print(json.dumps({
            "bounds": {
                "west": bounds.left,
                "south": bounds.bottom,
                "east": bounds.right,
                "north": bounds.top
            },
            "crs": str(src.crs)
        }))
except Exception as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(1)

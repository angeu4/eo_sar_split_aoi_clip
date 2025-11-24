# /worker/metadata_service.py
from flask import Flask, request, jsonify
import rasterio

app = Flask(__name__)

@app.route("/metadata", methods=["GET"])
def metadata():
    image_path = request.args.get("path")
    if not image_path:
        return jsonify({"error": "Missing path"}), 400
    try:
        with rasterio.open(image_path) as src:
            bounds = src.bounds
            return jsonify({
                "bounds": {
                    "west": bounds.left,
                    "south": bounds.bottom,
                    "east": bounds.right,
                    "north": bounds.top
                },
                "crs": str(src.crs)
            })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

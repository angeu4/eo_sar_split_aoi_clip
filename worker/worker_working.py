# /worker/worker.py
import argparse
import json
import os
import time
import traceback
import tempfile
from datetime import datetime

import rasterio
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling
import numpy as np
from skimage.registration import phase_cross_correlation
from shapely.geometry import box, mapping

DATA_DIR = "/data"
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
OUTPUT_DIR = os.path.join(DATA_DIR, "outputs")
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")


def read_jobs():
    try:
        if not os.path.exists(JOBS_FILE):
            return {}
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            if not txt:
                return {}
            return json.loads(txt)
    except Exception as e:
        print("read_jobs error:", e, flush=True)
        return {}


def write_jobs(jobs):
    # atomic write
    tmpfd, tmppath = tempfile.mkstemp(dir=os.path.dirname(JOBS_FILE))
    with os.fdopen(tmpfd, "w", encoding="utf-8") as tf:
        json.dump(jobs, tf, indent=2)
    os.replace(tmppath, JOBS_FILE)


def clip_image_working(path, aoi, out_path):
    with rasterio.open(path) as src:
        geom = box(aoi["west"], aoi["south"], aoi["east"], aoi["north"])

        # Check overlap first
        raster_bounds = src.bounds
        aoi_bounds = geom.bounds
        if (aoi_bounds[2] < raster_bounds.left or
            aoi_bounds[0] > raster_bounds.right or
            aoi_bounds[3] < raster_bounds.bottom or
            aoi_bounds[1] > raster_bounds.top):
            raise ValueError(f"AOI {aoi_bounds} does not overlap raster {raster_bounds}")

        out_img, out_transform = mask(src, [mapping(geom)], crop=True)
        out_meta = src.meta.copy()
        out_meta.update(
            {
                "driver": "GTiff",
                "height": out_img.shape[1],
                "width": out_img.shape[2],
                "transform": out_transform,
            }
        )
        with rasterio.open(out_path, "w", **out_meta) as dst:
            dst.write(out_img)

from pyproj import Transformer

def clip_image(path, aoi, out_path):
    with rasterio.open(path) as src:
        raster_crs = src.crs
        geom = box(aoi["west"], aoi["south"], aoi["east"], aoi["north"])

        # If AOI is in EPSG:4326 and raster is not, reproject AOI
        if str(raster_crs) != "EPSG:4326":
            transformer = Transformer.from_crs("EPSG:4326", raster_crs, always_xy=True)
            minx, miny = transformer.transform(aoi["west"], aoi["south"])
            maxx, maxy = transformer.transform(aoi["east"], aoi["north"])
            geom = box(minx, miny, maxx, maxy)

        raster_bounds = src.bounds
        if (geom.bounds[2] < raster_bounds.left or
            geom.bounds[0] > raster_bounds.right or
            geom.bounds[3] < raster_bounds.bottom or
            geom.bounds[1] > raster_bounds.top):
            raise ValueError(f"AOI {geom.bounds} does not overlap raster {raster_bounds}")

        out_img, out_transform = mask(src, [mapping(geom)], crop=True)
        out_meta = src.meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": out_img.shape[1],
            "width": out_img.shape[2],
            "transform": out_transform,
        })
        with rasterio.open(out_path, "w", **out_meta) as dst:
            dst.write(out_img)


def align_images_working(ref_path, mov_path, out_path):
    # Very simple: cross-correlation shift then reproject moving onto reference geotransform/crs
    with rasterio.open(ref_path) as ref, rasterio.open(mov_path) as mov:
        # read first band for registration (simple)
        ref_arr = ref.read(1, out_dtype="float32", masked=False)
        mov_arr = mov.read(1, out_dtype="float32", masked=False)

        # Replace NaNs with zeros for registration
        ref_arr = np.nan_to_num(ref_arr)
        mov_arr = np.nan_to_num(mov_arr)

        shift, error, diffphase = phase_cross_correlation(ref_arr, mov_arr, upsample_factor=10)
        print("Estimated shift (row, col):", shift, "error:", error, flush=True)

        # Reproject mov to ref's transform & crs
        out_meta = mov.meta.copy()
        out_meta.update({"crs": ref.crs, "transform": ref.transform, "height": ref.height, "width": ref.width})

        # Destination array shape and dtype
        dst_arr = np.zeros((mov.count, ref.height, ref.width), dtype=mov.dtypes[0])

        with rasterio.open(out_path, "w", **out_meta) as dst:
            # reproject all bands
            reproject(
                source=mov.read(),
                destination=dst_arr,
                src_transform=mov.transform,
                src_crs=mov.crs,
                dst_transform=ref.transform,
                dst_crs=ref.crs,
                resampling=Resampling.nearest,
            )
            dst.write(dst_arr)

def align_images_working2(ref_path, mov_path, out_path):
    with rasterio.open(ref_path) as ref, rasterio.open(mov_path) as mov:
        ref_arr = np.nan_to_num(ref.read(1, out_dtype="float32", masked=False))

        mov_arr = mov.read(1, out_dtype="float32", masked=False)

        # 🔧 Force moving image to same shape as reference
        dst_arr = np.empty_like(ref_arr, dtype="float32")
        reproject(
            source=mov_arr,
            destination=dst_arr,
            src_transform=mov.transform,
            src_crs=mov.crs,
            dst_transform=ref.transform,
            dst_crs=ref.crs,
            resampling=Resampling.bilinear,
        )
        mov_arr = dst_arr

        # Now both arrays are same shape
        shift, error, diffphase = phase_cross_correlation(ref_arr, mov_arr, upsample_factor=10)
        print("Estimated shift:", shift, "error:", error, flush=True)

        # Save aligned result
        out_meta = ref.meta.copy()
        out_meta.update({"dtype": ref.dtypes[0], "driver": "GTiff"})
        with rasterio.open(out_path, "w", **out_meta) as dst:
            dst.write(mov_arr, 1)

def align_images(ref_path, mov_path, out_path):
    with rasterio.open(ref_path) as ref, rasterio.open(mov_path) as mov:
        # Read reference band
        ref_arr = np.nan_to_num(ref.read(1, out_dtype="float32", masked=False))

        # Prepare array to hold reprojected moving image in ref's grid
        mov_arr_resampled = np.empty_like(ref_arr, dtype="float32")

        # 🔧 Force moving image into reference's shape/CRS/grid
        reproject(
            source=mov.read(1, out_dtype="float32", masked=False),
            destination=mov_arr_resampled,
            src_transform=mov.transform,
            src_crs=mov.crs,
            dst_transform=ref.transform,
            dst_crs=ref.crs,
            resampling=Resampling.bilinear,
        )

        # Now both have identical shape
        shift, error, diffphase = phase_cross_correlation(
            ref_arr, mov_arr_resampled, upsample_factor=10
        )
        print(f"Estimated shift={shift}, error={error}", flush=True)

        # Apply shift (optional: for now we just keep aligned via reprojection)
        aligned_arr = mov_arr_resampled

        # Save aligned raster with ref's metadata
        out_meta = ref.meta.copy()
        out_meta.update({
            "dtype": "float32",
            "driver": "GTiff",
            "height": ref.height,
            "width": ref.width,
            "transform": ref.transform,
            "crs": ref.crs,
            "count": 1
        })

        with rasterio.open(out_path, "w", **out_meta) as dst:
            dst.write(aligned_arr, 1)

def process_job_once(job_id, job):
    print(f"[{datetime.utcnow().isoformat()}] Processing job {job_id}", flush=True)
    aoi = job.get("aoi")
    if not aoi:
        raise ValueError("No AOI provided in job")

    imageAId = job.get("imageAId")
    imageBId = job.get("imageBId")
    if not imageAId or not imageBId:
        raise ValueError("Missing image IDs in job")

    image_a_path = os.path.join(UPLOAD_DIR, imageAId)
    image_b_path = os.path.join(UPLOAD_DIR, imageBId)

    if not os.path.exists(image_a_path):
        raise FileNotFoundError(f"Image A not found: {image_a_path}")
    if not os.path.exists(image_b_path):
        raise FileNotFoundError(f"Image B not found: {image_b_path}")

    out_job_dir = os.path.join(OUTPUT_DIR, job_id)
    os.makedirs(out_job_dir, exist_ok=True)

    a_out = os.path.join(out_job_dir, "A_clipped.tif")
    b_out = os.path.join(out_job_dir, "B_clipped.tif")
    b_aligned = os.path.join(out_job_dir, "B_clipped_aligned.tif")

    # Clip A and B
    clip_image(image_a_path, aoi, a_out)
    clip_image(image_b_path, aoi, b_out)

    # Align B->A
    align_images(a_out, b_out, b_aligned)

    # Outputs returned as API paths so frontend can fetch /api/outputs/...
    outputs = {
        "imageAUrl": f"/api/outputs/{job_id}/A_clipped.tif",
        "imageBUrl": f"/api/outputs/{job_id}/B_clipped_aligned.tif",
    }
    return outputs


def daemon_loop(poll_interval=2.0):
    print("Worker daemon started, polling for jobs...", flush=True)
    while True:
        try:
            jobs = read_jobs()
            changed = False
            for job_id, job in list(jobs.items()):
                if job.get("status") != "Pending":
                    continue

                # Claim job
                jobs[job_id]["status"] = "Running"
                jobs[job_id]["worker_started_at"] = datetime.utcnow().isoformat()
                write_jobs(jobs)
                print(f"Claimed job {job_id}", flush=True)

                try:
                    outputs = process_job_once(job_id, job)
                    jobs = read_jobs()  # reload in case of external changes
                    jobs[job_id]["status"] = "Done"
                    jobs[job_id]["outputs"] = outputs
                    jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
                    write_jobs(jobs)
                    print(f"Job {job_id} done", flush=True)
                except Exception as e:
                    tb = traceback.format_exc()
                    print(f"Job {job_id} failed: {e}\n{tb}", flush=True)
                    jobs = read_jobs()
                    jobs[job_id]["status"] = "Error"
                    jobs[job_id]["error"] = f"{str(e)}"
                    jobs[job_id]["error_trace"] = tb
                    write_jobs(jobs)

            time.sleep(poll_interval)
        except Exception as e:
            print("Worker main loop error:", e, flush=True)
            time.sleep(2.0)


def run_once_cli(args):
    # expects args.image_a, args.image_b, args.aoi, args.out_dir
    aoi_text = args.aoi
    try:
        aoi_parts = dict(item.split("=") for item in aoi_text.split(";"))
        aoi = {k: float(v) for k, v in aoi_parts.items()}
    except Exception as e:
        raise ValueError(f"Invalid AOI format: {args.aoi}") from e

    job = {
        "aoi": aoi,
        "imageAId": os.path.basename(args.image_a),
        "imageBId": os.path.basename(args.image_b),
    }
    # call process_job_once but with provided paths
    os.makedirs(args.out_dir, exist_ok=True)
    a_out = os.path.join(args.out_dir, "A_clipped.tif")
    b_out = os.path.join(args.out_dir, "B_clipped.tif")
    b_aligned = os.path.join(args.out_dir, "B_clipped_aligned.tif")

    clip_image(args.image_a, aoi, a_out)
    clip_image(args.image_b, aoi, b_out)
    align_images(a_out, b_out, b_aligned)
    print("Done (single-run mode). Outputs in:", args.out_dir, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image_a")
    parser.add_argument("--image_b")
    parser.add_argument("--aoi")
    parser.add_argument("--out_dir")
    parser.add_argument("--once", action="store_true", help="Run one-off (not daemon).")
    args = parser.parse_args()

    # Ensure data directories exist
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Ensure jobs.json exists
    if not os.path.exists(JOBS_FILE):
        with open(JOBS_FILE, "w") as f:
            f.write("{}")

    if args.image_a and args.image_b and args.aoi and args.out_dir:
        run_once_cli(args)
    else:
        daemon_loop(poll_interval=2.0)

import argparse
import json
import os
import time
import traceback
from datetime import datetime

import rasterio
from rasterio.mask import mask
from rasterio.warp import reproject, Resampling
import numpy as np
from skimage.registration import phase_cross_correlation
from shapely.geometry import box, mapping
from pyproj import Transformer

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
    tmp = JOBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as tf:
        json.dump(jobs, tf, indent=2)
    os.replace(tmp, JOBS_FILE)


def clip_image(path, aoi, out_path):
    """Clip raster by AOI, reprojecting AOI if CRS mismatches."""
    with rasterio.open(path) as src:
        raster_crs = src.crs
        geom = box(aoi["west"], aoi["south"], aoi["east"], aoi["north"])

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


def align_images(ref_path, mov_path, out_path):
    """Align moving image to reference image using reprojection + phase correlation."""
    with rasterio.open(ref_path) as ref, rasterio.open(mov_path) as mov:
        ref_arr = np.nan_to_num(ref.read(1, out_dtype="float32", masked=False))

        mov_arr_resampled = np.empty_like(ref_arr, dtype="float32")
        reproject(
            source=mov.read(1, out_dtype="float32", masked=False),
            destination=mov_arr_resampled,
            src_transform=mov.transform,
            src_crs=mov.crs,
            dst_transform=ref.transform,
            dst_crs=ref.crs,
            resampling=Resampling.bilinear,
        )

        # Default: keep resampled array
        aligned_arr = mov_arr_resampled

        try:
            shift, error, diffphase = phase_cross_correlation(
                ref_arr, mov_arr_resampled, upsample_factor=10
            )
            print(f"[align_images] shift={shift}, error={error}", flush=True)
            # NOTE: For now, we don’t apply sub-pixel shift, just log it.
        except Exception as e:
            print("[align_images] Phase correlation failed:", e, flush=True)

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
        raise ValueError("No AOI provided")

    imageAId, imageBId = job.get("imageAId"), job.get("imageBId")
    if not imageAId or not imageBId:
        raise ValueError("Missing image IDs")

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

    clip_image(image_a_path, aoi, a_out)
    clip_image(image_b_path, aoi, b_out)
    align_images(a_out, b_out, b_aligned)

    return {
        "imageAUrl": f"/api/outputs/{job_id}/A_clipped.tif",
        "imageBUrl": f"/api/outputs/{job_id}/B_clipped_aligned.tif",
    }


def daemon_loop():
    print("Worker daemon started", flush=True)
    while True:
        try:
            jobs = read_jobs()
            for job_id, job in jobs.items():
                if job.get("status") != "Pending":
                    continue

                jobs[job_id]["status"] = "Running"
                jobs[job_id]["worker_started_at"] = datetime.utcnow().isoformat()
                write_jobs(jobs)

                try:
                    outputs = process_job_once(job_id, job)
                    jobs = read_jobs()
                    jobs[job_id]["status"] = "Done"
                    jobs[job_id]["outputs"] = outputs
                    jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
                    write_jobs(jobs)
                except Exception as e:
                    tb = traceback.format_exc()
                    jobs = read_jobs()
                    jobs[job_id]["status"] = "Error"
                    jobs[job_id]["error"] = str(e)
                    jobs[job_id]["error_trace"] = tb
                    jobs[job_id]["finished_at"] = datetime.utcnow().isoformat()
                    write_jobs(jobs)
            time.sleep(2.0)
        except Exception as e:
            print("Worker loop error:", e, flush=True)
            time.sleep(2.0)


if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if not os.path.exists(JOBS_FILE):
        with open(JOBS_FILE, "w") as f:
            f.write("{}")
    daemon_loop()

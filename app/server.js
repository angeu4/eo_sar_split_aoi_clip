// /app/server.js
import express from "express";
import multer from "multer";
import fs from "fs";
import path from "path";
import os from "os";
import { spawnSync } from "child_process";

const app = express();
app.disable("etag");
app.use(express.json());

const DATA_DIR = "/data";
const UPLOAD_DIR = path.join(DATA_DIR, "uploads");
const OUTPUT_DIR = path.join(DATA_DIR, "outputs");
const JOBS_FILE = path.join(DATA_DIR, "jobs.json");

// Ensure dirs exist
[UPLOAD_DIR, OUTPUT_DIR].forEach((d) => fs.mkdirSync(d, { recursive: true }));
if (!fs.existsSync(JOBS_FILE)) fs.writeFileSync(JOBS_FILE, "{}");

// Helpers: atomic read/write for jobs.json
function readJobs() {
  try {
    if (!fs.existsSync(JOBS_FILE)) return {};
    const txt = fs.readFileSync(JOBS_FILE, "utf8");
    if (!txt) return {};
    return JSON.parse(txt);
  } catch (err) {
    console.error("readJobs error:", err);
    return {};
  }
}

function writeJobs(jobs) {
  const tmp = JOBS_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(jobs, null, 2));
  fs.renameSync(tmp, JOBS_FILE);
}

// Multer disk storage (stream to disk). Limit set to 2GB.
const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, UPLOAD_DIR),
  filename: (req, file, cb) => cb(null, Date.now() + "-" + file.originalname),
});
const upload = multer({
  storage,
  limits: { fileSize: 2000 * 1024 * 1024 }, // 2 GB
});

/** Upload endpoint */
app.post("/api/upload", upload.single("file"), (req, res) => {
  if (!req.file) return res.status(400).json({ error: "No file uploaded" });
  const fileId = path.basename(req.file.filename);
  res.json({ imageId: fileId, size: req.file.size });
});

/** Create job (no longer spawns anything) */
app.post("/api/jobs", (req, res) => {
  const { imageAId, imageBId, aoi } = req.body;
  if (!imageAId || !imageBId || !aoi) {
    return res.status(400).json({ error: "Missing parameters: imageAId, imageBId, aoi" });
  }
  // Basic AOI sanity check
  if (!(aoi.north > aoi.south && aoi.east > aoi.west)) {
    return res.status(400).json({ error: "Invalid AOI bounds (north>south, east>west required)" });
  }

  const jobs = readJobs();
  const jobId = Date.now().toString();
  jobs[jobId] = {
    status: "Pending",
    imageAId,
    imageBId,
    aoi,
    created_at: new Date().toISOString(),
  };
  writeJobs(jobs);

  // Return immediately — worker will pick up the job
  res.json({ jobId });
});

/** Job status */
app.get("/api/jobs/:jobId", (req, res) => {
  const jobs = readJobs();
  const job = jobs[req.params.jobId];
  if (!job) return res.status(404).json({ error: "Job not found" });
  res.json(job);
});

app.get("/api/metadata/:imageId", async (req, res) => {
  const imageId = req.params.imageId;
  const filePath = path.join(UPLOAD_DIR, imageId);
  if (!fs.existsSync(filePath)) {
    return res.status(404).json({ error: "Image not found" });
  }

  try {
    const response = await fetch(`http://worker:5000/metadata?path=${filePath}`);
    const data = await response.json();
    res.json(data);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});





// Serve processed outputs under /api/outputs so nginx proxying works.
app.use("/api/outputs", express.static(OUTPUT_DIR));

const PORT = 8080;
app.listen(PORT, () => console.log(`API running on port ${PORT}`));

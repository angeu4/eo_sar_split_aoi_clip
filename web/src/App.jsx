import { useState, useEffect } from "react";
import { MapContainer, TileLayer, Rectangle, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";

export default function App() {
  const [files, setFiles] = useState({});
  const [metadata, setMetadata] = useState({});
  const [job, setJob] = useState(null);
  const [status, setStatus] = useState(null);
  const [aoi, setAoi] = useState({ north: null, south: null, east: null, west: null });

  const handleUpload = async (e, key) => {
    const file = e.target.files[0];
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await res.json();
    setFiles(prev => ({ ...prev, [key]: data.imageId }));

    // Fetch metadata for this file
    const metaRes = await fetch(`/api/metadata/${data.imageId}`);
    const meta = await metaRes.json();
    setMetadata(prev => ({ ...prev, [key]: meta.bounds }));
  };

  const handleProcess = async () => {
    if (!aoi.north || !aoi.south || !aoi.east || !aoi.west) {
      alert("Please enter AOI values first");
      return;
    }

    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        imageAId: files.A,
        imageBId: files.B,
        aoi,
      }),
    });
    const data = await res.json();
    setJob(data.jobId);
  };

  useEffect(() => {
    if (!job) return;
    const interval = setInterval(async () => {
      const res = await fetch(`/api/jobs/${job}`);
      const data = await res.json();
      setStatus(data);
      if (data.status === "Done" || data.status === "Error") {
        clearInterval(interval);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [job]);

  return (
    <div className="p-4">
      <h1>Split-View EO/SAR Map</h1>
      <div>
        <input type="file" onChange={e => handleUpload(e, "A")} />
        <input type="file" onChange={e => handleUpload(e, "B")} />
      </div>

      <h3 className="mt-4">Raster Bounds</h3>
      <pre>{JSON.stringify(metadata, null, 2)}</pre>

      <h3 className="mt-4">Enter AOI</h3>
      <div>
        <label>North:</label>
        <input type="number" step="0.0001" value={aoi.north || ""} onChange={e => setAoi({ ...aoi, north: parseFloat(e.target.value) })} />
        <label>South:</label>
        <input type="number" step="0.0001" value={aoi.south || ""} onChange={e => setAoi({ ...aoi, south: parseFloat(e.target.value) })} />
        <label>East:</label>
        <input type="number" step="0.0001" value={aoi.east || ""} onChange={e => setAoi({ ...aoi, east: parseFloat(e.target.value) })} />
        <label>West:</label>
        <input type="number" step="0.0001" value={aoi.west || ""} onChange={e => setAoi({ ...aoi, west: parseFloat(e.target.value) })} />
      </div>

      <button onClick={handleProcess} disabled={!files.A || !files.B}>
        Process AOI
      </button>

      {status && <pre>{JSON.stringify(status, null, 2)}</pre>}

      <MapContainer style={{ height: "400px", width: "100%" }} center={[0, 0]} zoom={2}>
        <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        {aoi.north && (
          <Rectangle
            bounds={[
              [aoi.south, aoi.west],
              [aoi.north, aoi.east],
            ]}
            pathOptions={{ color: "red" }}
          />
        )}
      </MapContainer>
    </div>
  );
}

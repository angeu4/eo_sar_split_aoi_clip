import { useState, useEffect } from "react";
import { MapContainer, TileLayer } from "react-leaflet";
import "leaflet/dist/leaflet.css";

export default function App() {
  const [files, setFiles] = useState({});
  const [job, setJob] = useState(null);
  const [status, setStatus] = useState(null);

  const handleUpload = async (e, key) => {
    const file = e.target.files[0];
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/upload", {
      method: "POST",
      body: formData,
    });
    const data = await res.json();
    setFiles(prev => ({ ...prev, [key]: data.imageId }));
  };

  const handleProcess = async () => {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        imageAId: files.A,
        imageBId: files.B,
        //aoi: { north: 10, south: -10, east: 10, west: -10 }
        aoi: { north: 28.64, south: 28.60, east: 77.23, west: 77.18 }
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

      // Stop polling if job reached a terminal state
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
      <button onClick={handleProcess} disabled={!files.A || !files.B}>
        Process AOI
      </button>
      {status && <pre>{JSON.stringify(status, null, 2)}</pre>}

      <MapContainer style={{ height: "400px", width: "100%" }} center={[0,0]} zoom={2}>
        <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      </MapContainer>
    </div>
  );
}

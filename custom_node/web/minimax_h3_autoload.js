import { app } from "../../scripts/app.js";

const WORKFLOW_URL = "/extensions/minimax_h3_autoload/video_minimax_h3_i2v.json";

async function loadMiniMaxH3I2V() {
  if (new URLSearchParams(window.location.search).get("autoload_i2v") === "0") {
    return;
  }

  try {
    const response = await fetch(`${WORKFLOW_URL}?ts=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const workflow = await response.json();
    if (!Array.isArray(workflow.nodes) || !Array.isArray(workflow.links)) {
      throw new Error("workflow JSON is not a ComfyUI graph");
    }
    await app.loadGraphData(workflow, true, true, "video_minimax_h3_i2v");
    console.info("[MiniMax H3] Auto-loaded video_minimax_h3_i2v");
  } catch (error) {
    console.error("[MiniMax H3] Failed to auto-load I2V workflow", error);
  }
}

app.registerExtension({
  name: "minimax_h3_autoload",
  async setup() {
    window.setTimeout(loadMiniMaxH3I2V, 1500);
  },
});

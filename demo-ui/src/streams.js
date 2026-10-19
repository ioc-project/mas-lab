/** Named telemetry streams plugins get() / subscribe() on.
 *  Keep in sync with lab/components/controller/src/mas/lab/controller/ui_stream/streams.py
 */

export const STREAM_TOPICS = {
  spec: ["spec.snapshot", "spec.*"],
  observability: ["native.*"],
  metrics: ["metrics.snapshot", "metric.*", "native.metric", "metric.scope"],
  artifacts: ["artifact.*", "artifact.metric"],
  governance: ["governance.*", "control.context"],
  control: ["control.*", "control.structure", "control.context"],
  hitl: ["control.*"],
  job: ["job.*", "job.progress"],
  custom: ["custom.*"],
  series: ["series.*"],
};

export function expandStreamNames(names) {
  const out = [];
  const seen = new Set();
  for (const raw of names || []) {
    const text = String(raw || "").trim();
    if (!text || text === "*") continue;
    let parts = STREAM_TOPICS[text];
    if (!parts && (text.startsWith("custom.") || text.startsWith("series.")) && !text.includes("*")) {
      parts = [text, `${text}.*`];
    }
    parts = parts || [text];
    for (const item of parts) {
      if (seen.has(item)) continue;
      seen.add(item);
      out.push(item);
    }
  }
  return out;
}

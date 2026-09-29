package ai.carrot.jetlink

import org.json.JSONObject

/** Model/backend selections may change later; evidence always retains its execution identity. */
class MeasurementRun(private val modelSha: String?, private val sourceSha: String?,
    private val backend: String, private val scope: String) {
    val stats = FrameStats()
    fun report(): JSONObject {
        val s = stats.snapshot()
        return JSONObject().apply {
            put("model_sha256", modelSha ?: JSONObject.NULL); put("source_sha256", sourceSha ?: JSONObject.NULL)
            put("backend_requested", backend); put("provider_partitioning_verified", false)
            put("measurement_scope", scope); put("frames", s.frames); put("over_50ms", s.overBudget)
            put("mean_ms", s.mean); put("p50_ms", s.p50); put("p95_ms", s.p95); put("p99_ms", s.p99); put("max_ms", s.maximum)
            put("percentile_scope", "last 2000 app samples; mean/max/misses cover the whole run")
            put("scope", "Excludes C3X warp and full link latency. Not vehicle acceptance.")
        }
    }
}

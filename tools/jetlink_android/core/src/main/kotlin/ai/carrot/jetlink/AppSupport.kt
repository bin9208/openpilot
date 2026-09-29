package ai.carrot.jetlink

import kotlin.math.ceil

class PermissionGate {
    private var generation = 0L
    private var device: Int? = null
    @Synchronized fun begin(deviceId: Int): Long { generation++; device = deviceId; return generation }
    @Synchronized fun cancel() { generation++; device = null }
    @Synchronized fun consume(token: Long, deviceId: Int): Boolean {
        if (token != generation || device != deviceId) return false
        device = null
        return true
    }
}

data class FrameSummary(val frames: Long, val overBudget: Long, val mean: Double, val p50: Double, val p95: Double, val p99: Double, val maximum: Double)

class FrameStats(private val capacity: Int = 2000) {
    init { require(capacity in 1..10000) }
    private val samples = DoubleArray(capacity)
    private var count = 0L
    private var misses = 0L
    private var total = 0.0
    private var maximum = 0.0
    @Synchronized fun record(milliseconds: Double) {
        require(milliseconds.isFinite() && milliseconds >= 0)
        samples[(count % capacity).toInt()] = milliseconds
        count++; total += milliseconds
        if (milliseconds > 50) misses++
        maximum = maxOf(maximum, milliseconds)
    }
    @Synchronized fun snapshot(): FrameSummary {
        val used = samples.copyOf(minOf(count, capacity.toLong()).toInt()).sortedArray()
        fun percentile(fraction: Double) = if (used.isEmpty()) 0.0 else used[(ceil(fraction * used.size).toInt() - 1).coerceAtLeast(0)]
        return FrameSummary(count, misses, if (count == 0L) 0.0 else total / count, percentile(.5), percentile(.95), percentile(.99), maximum)
    }
}

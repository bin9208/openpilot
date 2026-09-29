package ai.carrot.jetlink

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.hardware.usb.UsbManager
import android.os.IBinder
import android.os.PowerManager
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicBoolean

/** USB is owned by one worker. Stop never waits for native inference on the UI thread. */
class LinkService : Service() {
    private val stopping = AtomicBoolean(false)
    @Volatile private var link: UsbLink? = null
    private var worker: Thread? = null

    override fun onBind(intent: Intent?): IBinder? = null
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == STOP) { requestStop(); return START_NOT_STICKY }
        if (worker != null) return START_NOT_STICKY
        val token = intent?.getLongExtra("token", -1) ?: -1
        if (token < 0 || token != AppState.connectionToken || AppState.phase != "connecting") {
            stopSelf(); return START_NOT_STICKY
        }
        try {
            val manager = getSystemService(UsbManager::class.java)
            val device = manager.deviceList[intent?.getStringExtra("device")]
                ?: error(getString(R.string.no_usb))
            require(manager.hasPermission(device)) { getString(R.string.usb_denied) }
            val notifications = getSystemService(NotificationManager::class.java)
            notifications.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.app_name), NotificationManager.IMPORTANCE_LOW))
            val stop = PendingIntent.getService(this, 0, Intent(this, LinkService::class.java).setAction(STOP), PendingIntent.FLAG_IMMUTABLE)
            val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
            startForeground(1, Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_link)
                .setContentTitle(getString(R.string.app_name)).setContentText(getString(R.string.linked))
                .setContentIntent(open).setOngoing(true).addAction(Notification.Action.Builder(null, getString(R.string.stop), stop).build()).build())
            stopping.set(false)
            AppState.stopConnection = { requestStop() }
            AppState.beginRun(this, "USB app processing and send; excludes request receive")
            val backend = AppState.backend
            worker = Thread({
                var session: JetlinkSession? = null
                val wake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "CarrotJetlink:usb")
                try {
                    if (stopping.get() || token != AppState.connectionToken) return@Thread
                    val model = ModelRepository.current(this, true) ?: error(getString(R.string.no_model))
                    if (stopping.get()) return@Thread
                    link = UsbLink.open(manager, device)
                    wake.acquire(60 * 60 * 1000L)
                    session = JetlinkSession(model, { OrtEngine(backend) }, { DeviceHealth.snapshot(this).apply {
                        put("device_model", android.os.Build.MODEL); put("android_api", android.os.Build.VERSION.SDK_INT)
                        put("backend_requested", backend.name); put("app_version", BuildConfig.VERSION_NAME)
                        put("artifact_sha256", model.artifactSha); put("source_sha256", model.sourceSha)
                    } })
                    AppState.phase = "linked"; AppState.detail = ""
                    while (!stopping.get()) {
                        if (DeviceHealth.tooHot(this)) error(getString(R.string.thermal_stop))
                        if (!wake.isHeld) wake.acquire(60 * 60 * 1000L)
                        for (update in session.drainUpdates()) link!!.write(update, System.nanoTime() + 2_000_000_000)
                        val message = try { link!!.read(System.nanoTime() + 2_000_000_000) }
                            catch (_: SocketTimeoutException) { continue }
                        val before = session.framesServed
                        val started = System.nanoTime()
                        val replies = session.handle(message)
                        for (reply in replies) link!!.write(reply, System.nanoTime() + 2_000_000_000)
                        if (session.framesServed > before) AppState.stats.record((System.nanoTime() - started) / 1e6)
                        AppState.phase = if (session.state == "building") "preparing" else "linked"
                        AppState.detail = "Engine: ${session.state}"
                    }
                } catch (e: OutOfMemoryError) {
                    AppState.fail(getString(R.string.memory_error))
                } catch (e: Exception) {
                    if (!stopping.get()) AppState.fail(e.message ?: e.javaClass.simpleName)
                } finally {
                    link?.close(); link = null
                    session?.close()
                    if (wake.isHeld) wake.release()
                    AppState.stopConnection = null; AppState.connectionToken = -1
                    AppState.busy = false; AppState.deviceName = null
                    if (AppState.phase != "error") AppState.phase = "idle"
                    AppState.lastReport = AppState.report(this)
                    stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
                }
            }, "jetlink-usb").also { it.start() }
        } catch (e: Exception) {
            AppState.busy = false; AppState.fail(e.message ?: e.javaClass.simpleName)
            stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun requestStop() { stopping.set(true); link?.close() }
    override fun onDestroy() { requestStop(); super.onDestroy() }
    companion object { private const val CHANNEL = "jetlink_usb"; private const val STOP = "ai.carrot.jetlink.STOP" }
}

package ai.comma.naver.payload;

import java.io.File;
import java.io.IOException;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.ReentrantLock;
import java.util.UUID;

final class DiagnosticRuntime implements HookRuntime {
  private static final long SINK_CLOSE_TIMEOUT_MS = 500L;
  private static final long MAPPER_POLL_MS = 50L;

  interface AcceptanceObserver {
    void beforeQueueOffer();

    default void beforeClose() {
    }

    default void closeWaitingForAcceptance() {
    }

    default void afterAcceptanceClosed() {
    }

    default void beforeMapperJoin() {
    }

    default void mapperJoinWaiting() {
    }
  }

  interface MapperJoinStrategy {
    void join(Thread worker) throws InterruptedException;
  }

  interface ExtractionPublisher {
    boolean publish(long deadlineNanos);
  }

  static final AcceptanceObserver NO_ACCEPTANCE_OBSERVER = () -> { };
  static final MapperJoinStrategy DEFAULT_MAPPER_JOIN_STRATEGY = Thread::join;
  static final ExtractionPublisher NO_EXTRACTION_PUBLISHER = deadlineNanos -> true;

  private static final class Event {
    final String channel;
    final Object value;

    Event(String channel, Object value) {
      this.channel = channel;
      this.value = value;
    }
  }

  private final ReentrantLock acceptanceLock = new ReentrantLock();
  private final ReentrantLock extractionLock = new ReentrantLock();
  private final ArrayBlockingQueue<Event> queue;
  private final DiagnosticCounters counters;
  private final BoundedMappingDiagnostics diagnostics;
  private final DiagnosticFrameSink localSink;
  private final DiagnosticFrameSink networkSink;
  private final Thread worker;
  private final AcceptanceObserver acceptanceObserver;
  private final MapperJoinStrategy mapperJoinStrategy;
  private final ExtractionPublisher extractionPublisher;
  private volatile boolean closed;
  private int extractionState;

  static HookRuntime createDefault() {
    if (!DiagnosticConfig.MAIN_PROCESS.equals(AndroidProcess.currentName())) {
      return NoOpRuntime.INSTANCE;
    }
    AndroidCaptureSharing.grantReadAccess();
    DiagnosticCounters counters = new DiagnosticCounters();
    LocalFrameConsumer localConsumer = new LocalFrameConsumer(newRunId(), counters);
    DiagnosticFrameSink localSink = new AsyncDiagnosticSink(
        localConsumer,
        counters,
        AsyncDiagnosticSink.Role.LOCAL,
        DiagnosticConfig.OFFLINE_CAPTURE_LOCAL_QUEUE_CAPACITY,
        "naver-diagnostic-local");
    DiagnosticFrameSink networkSink = new AsyncDiagnosticSink(
        new NetworkFrameConsumer(new PersistentNavigationTransport(
            DiagnosticConfig.RECEIVER_HOST,
            DiagnosticConfig.RECEIVER_PORT,
            DiagnosticConfig.SOCKET_TIMEOUT_MS)),
        counters,
        AsyncDiagnosticSink.Role.NETWORK,
        DiagnosticConfig.OFFLINE_CAPTURE_NETWORK_QUEUE_CAPACITY,
        DiagnosticConfig.NETWORK_THREAD_NAME);
    return new DiagnosticRuntime(
        new BoundedMappingDiagnostics(DiagnosticConfig.MAX_CHANNEL_CHARS),
        counters,
        localSink,
        networkSink,
        DiagnosticConfig.QUEUE_CAPACITY,
        "naver-diagnostic-mapper",
        NO_ACCEPTANCE_OBSERVER,
        DEFAULT_MAPPER_JOIN_STRATEGY,
        localConsumer::publishSnapshot);
  }

  DiagnosticRuntime(
      BoundedMappingDiagnostics diagnostics,
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      int queueCapacity,
      String threadName) {
    this(diagnostics, new DiagnosticCounters(), localSink, networkSink, queueCapacity, threadName,
        NO_ACCEPTANCE_OBSERVER, DEFAULT_MAPPER_JOIN_STRATEGY);
  }

  DiagnosticRuntime(
      BoundedMappingDiagnostics diagnostics,
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      int queueCapacity,
      String threadName,
      AcceptanceObserver acceptanceObserver) {
    this(diagnostics, new DiagnosticCounters(), localSink, networkSink, queueCapacity, threadName,
        acceptanceObserver, DEFAULT_MAPPER_JOIN_STRATEGY);
  }

  DiagnosticRuntime(
      BoundedMappingDiagnostics diagnostics,
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      int queueCapacity,
      String threadName,
      AcceptanceObserver acceptanceObserver,
      MapperJoinStrategy mapperJoinStrategy) {
    this(diagnostics, new DiagnosticCounters(), localSink, networkSink, queueCapacity, threadName,
        acceptanceObserver, mapperJoinStrategy, NO_EXTRACTION_PUBLISHER);
  }

  DiagnosticRuntime(
      BoundedMappingDiagnostics diagnostics,
      DiagnosticCounters counters,
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      int queueCapacity,
      String threadName,
      AcceptanceObserver acceptanceObserver,
      MapperJoinStrategy mapperJoinStrategy) {
    this(diagnostics, counters, localSink, networkSink, queueCapacity, threadName,
        acceptanceObserver, mapperJoinStrategy, NO_EXTRACTION_PUBLISHER);
  }

  DiagnosticRuntime(
      BoundedMappingDiagnostics diagnostics,
      DiagnosticCounters counters,
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      int queueCapacity,
      String threadName,
      AcceptanceObserver acceptanceObserver,
      MapperJoinStrategy mapperJoinStrategy,
      ExtractionPublisher extractionPublisher) {
    if (diagnostics == null || counters == null || localSink == null || networkSink == null
        || threadName == null || acceptanceObserver == null || mapperJoinStrategy == null
        || extractionPublisher == null
        || queueCapacity != DiagnosticConfig.QUEUE_CAPACITY) {
      throw new IllegalArgumentException("invalid diagnostic runtime configuration");
    }
    this.diagnostics = diagnostics;
    this.counters = counters;
    this.localSink = localSink;
    this.networkSink = networkSink;
    this.acceptanceObserver = acceptanceObserver;
    this.mapperJoinStrategy = mapperJoinStrategy;
    this.extractionPublisher = extractionPublisher;
    queue = new ArrayBlockingQueue<>(queueCapacity);
    worker = new Thread(this::runWorker, threadName);
    worker.setDaemon(true);
    worker.start();
  }

  @Override
  public void enqueue(String channel, Object value) {
    if (!acceptanceLock.tryLock()) {
      counters.addObjectQueueDropped(1L);
      return;
    }
    try {
      if (closed) {
        return;
      }
      acceptanceObserver.beforeQueueOffer();
      if (!queue.offer(new Event(channel, value))) {
        counters.addObjectQueueDropped(1L);
      }
    } finally {
      acceptanceLock.unlock();
    }
  }

  private void runWorker() {
    while (!closed || !queue.isEmpty()) {
      Event event;
      try {
        event = queue.poll(MAPPER_POLL_MS, TimeUnit.MILLISECONDS);
      } catch (InterruptedException interrupted) {
        continue;
      }
      if (event == null) {
        continue;
      }
      try {
        EncodedDiagnosticFrame frame = diagnostics.encode(
            event.channel, event.value, counters.snapshot().objectQueueDropped);
        localSink.offer(frame);
        networkSink.offer(frame);
      } catch (RuntimeException ignored) {
        counters.addRejectedFrames(1L);
        // Mapping failures are isolated from the hook and do not stop later frames.
      }
    }
  }

  @Override
  public void close() {
    acceptanceObserver.beforeClose();
    if (!acceptanceLock.tryLock()) {
      acceptanceObserver.closeWaitingForAcceptance();
      acceptanceLock.lock();
    }
    try {
      if (closed) {
        return;
      }
      closed = true;
      acceptanceObserver.afterAcceptanceClosed();
    } finally {
      acceptanceLock.unlock();
    }
    worker.interrupt();
    acceptanceObserver.beforeMapperJoin();
    join();
    localSink.drainAndClose(SINK_CLOSE_TIMEOUT_MS);
    networkSink.drainAndClose(SINK_CLOSE_TIMEOUT_MS);
  }

  @Override
  public boolean quiesceForExtraction(long timeoutMillis) {
    if (timeoutMillis <= 0L) {
      return false;
    }
    long deadlineNanos =
        System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
    boolean extractionLocked = false;
    try {
      extractionLocked = extractionLock.tryLock(
          Math.max(0L, deadlineNanos - System.nanoTime()), TimeUnit.NANOSECONDS);
      if (!extractionLocked) {
        return false;
      }
      if (extractionState == 2) {
        return true;
      }
      if (extractionState == 3) {
        return false;
      }
      extractionState = 1;
      closed = true;
      boolean acceptanceFrozen = false;
      try {
        acceptanceFrozen = acceptanceLock.tryLock(
            remainingMillis(deadlineNanos), TimeUnit.MILLISECONDS);
        if (!acceptanceFrozen) {
          extractionState = 3;
          return false;
        }
      } catch (InterruptedException interrupted) {
        Thread.currentThread().interrupt();
        extractionState = 3;
        return false;
      } finally {
        if (acceptanceFrozen) {
          acceptanceLock.unlock();
        }
      }
      worker.interrupt();
      if (!joinUntil(deadlineNanos)
          || !localSink.quiesceAndClose(remainingMillis(deadlineNanos))) {
        extractionState = 3;
        return false;
      }
      if (!networkSink.quiesceAndClose(remainingMillis(deadlineNanos))) {
        extractionState = 3;
        return false;
      }
      try {
        if (!extractionPublisher.publish(deadlineNanos)) {
          extractionState = 3;
          return false;
        }
      } catch (RuntimeException ignored) {
        extractionState = 3;
        return false;
      }
      extractionState = 2;
      return true;
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      return false;
    } finally {
      if (extractionLocked) {
        extractionLock.unlock();
      }
    }
  }

  private boolean joinUntil(long deadlineNanos) {
    boolean interrupted = false;
    try {
      while (worker.isAlive()) {
        long remaining = remainingMillis(deadlineNanos);
        if (remaining == 0L) {
          return false;
        }
        try {
          worker.join(remaining);
        } catch (InterruptedException error) {
          interrupted = true;
        }
      }
      return true;
    } finally {
      if (interrupted) {
        Thread.currentThread().interrupt();
      }
    }
  }

  private static long remainingMillis(long deadlineNanos) {
    long remainingNanos = deadlineNanos - System.nanoTime();
    if (remainingNanos <= 0L) {
      return 0L;
    }
    return TimeUnit.NANOSECONDS.toMillis(remainingNanos);
  }

  private void join() {
    boolean interrupted = false;
    while (true) {
      try {
        if (worker.isAlive()) {
          acceptanceObserver.mapperJoinWaiting();
        }
        mapperJoinStrategy.join(worker);
        break;
      } catch (InterruptedException error) {
        interrupted = true;
      }
    }
    if (interrupted) {
      Thread.currentThread().interrupt();
    }
  }

  static String newRunId() {
    return UUID.randomUUID().toString();
  }

  private enum NoOpRuntime implements HookRuntime {
    INSTANCE;

    @Override
    public void enqueue(String channel, Object value) {
    }

    @Override
    public boolean quiesceForExtraction(long timeoutMillis) {
      return false;
    }

    @Override
    public void close() {
    }
  }

  private static final class LocalFrameConsumer implements FrameBatchConsumer {
    private final Object lifecycleLock = new Object();
    private final String runId;
    private final DiagnosticCounters counters;
    private OfflineCaptureStore store;
    private File canonicalDatabase;
    private boolean terminal;
    private boolean writing;

    LocalFrameConsumer(String runId, DiagnosticCounters counters) {
      this.runId = runId;
      this.counters = counters;
    }

    @Override
    public void writeBatch(List<EncodedDiagnosticFrame> frames, DiagnosticCounters.Snapshot counters)
        throws IOException {
      OfflineCaptureStore active = currentStore();
      synchronized (lifecycleLock) {
        if (terminal) {
          throw new IOException("capture store is closed");
        }
        writing = true;
      }
      try {
        active.writeBatch(frames, counters);
      } finally {
        synchronized (lifecycleLock) {
          writing = false;
          lifecycleLock.notifyAll();
        }
      }
    }

    @Override
    public void close() {
      OfflineCaptureStore active;
      synchronized (lifecycleLock) {
        terminal = true;
        while (writing) {
          try {
            lifecycleLock.wait();
          } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            return;
          }
        }
        active = store;
        store = null;
      }
      if (active != null) {
        active.close();
      }
    }

    boolean publishSnapshot(long deadlineNanos) {
      File source;
      synchronized (lifecycleLock) {
        if (!terminal || writing || store != null) {
          return false;
        }
        source = canonicalDatabase;
      }
      return source != null && AndroidCaptureSharing.publishSnapshot(source, deadlineNanos);
    }

    private OfflineCaptureStore currentStore() throws IOException {
      synchronized (lifecycleLock) {
        if (terminal) {
          throw new IOException("capture store is closed");
        }
        if (store != null) {
          return store;
        }
      }
      File directory = AndroidApplicationFiles.resolve(DiagnosticConfig.OFFLINE_CAPTURE_DIRECTORY_NAME);
      if (directory == null) {
        throw new IOException("capture internal directory is unavailable");
      }
      AndroidSQLiteCaptureDatabase database = AndroidSQLiteCaptureDatabase.open(directory);
      OfflineCaptureStore created = new OfflineCaptureStore(
          database,
          new CaptureRetentionPolicy(),
          runId,
          DiagnosticConfig.OFFLINE_CAPTURE_PAYLOAD_BUILD_ID,
          counters);
      synchronized (lifecycleLock) {
        if (terminal) {
          created.close();
          throw new IOException("capture store is closed");
        }
        if (store == null) {
          store = created;
          canonicalDatabase = database.snapshotSource();
          return created;
        }
        created.close();
        return store;
      }
    }
  }
}

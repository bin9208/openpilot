package ai.comma.naver.payload;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.ReentrantLock;

final class AsyncDiagnosticSink implements DiagnosticFrameSink {
  enum Role { LOCAL, NETWORK }

  interface AcceptanceObserver {
    void beforeQueueOffer();

    default void beforeClose() {
    }

    default void closeWaitingForAcceptance() {
    }

    default void afterAcceptanceClosed() {
    }
  }

  interface WaitStrategy {
    void await(long millis) throws InterruptedException;
  }

  interface ReservationObserver {
    void beforeReservation();
  }

  static final int BATCH_SIZE = DiagnosticConfig.OFFLINE_CAPTURE_BATCH_SIZE;
  static final long FLUSH_DEADLINE_MS = DiagnosticConfig.OFFLINE_CAPTURE_FLUSH_INTERVAL_MS;
  static final long RETRY_INITIAL_MS = DiagnosticConfig.OFFLINE_CAPTURE_RETRY_INITIAL_MS;
  static final long RETRY_MAX_MS = DiagnosticConfig.OFFLINE_CAPTURE_RETRY_MAX_MS;
  static final AcceptanceObserver NO_ACCEPTANCE_OBSERVER = () -> { };
  static final WaitStrategy DEFAULT_WAIT_STRATEGY = Thread::sleep;
  static final ReservationObserver NO_RESERVATION_OBSERVER = () -> { };

  private final ReentrantLock acceptanceLock = new ReentrantLock();
  private final Object consumerLifecycleLock = new Object();
  private final ArrayBlockingQueue<EncodedDiagnosticFrame> queue;
  private final FrameBatchConsumer consumer;
  private final DiagnosticCounters counters;
  private final Role role;
  private final Thread worker;
  private final AcceptanceObserver acceptanceObserver;
  private final WaitStrategy waitStrategy;
  private final ReservationObserver reservationObserver;
  private final CountDownLatch workerTerminated = new CountDownLatch(1);
  private final CountDownLatch terminalPublished = new CountDownLatch(1);
  private final CountDownLatch consumerCloseCompleted = new CountDownLatch(1);
  private final AtomicBoolean consumerCloseStarted = new AtomicBoolean();
  private final AtomicBoolean consumerCloseSucceeded = new AtomicBoolean();
  private volatile boolean accepting = true;
  private volatile boolean closing;
  private volatile boolean gracefulFailure;
  private boolean terminal;
  private boolean consumerCallActive;

  AsyncDiagnosticSink(
      FrameBatchConsumer consumer,
      DiagnosticCounters counters,
      Role role,
      int capacity,
      String threadName) {
    this(consumer, counters, role, capacity, threadName, NO_ACCEPTANCE_OBSERVER,
        DEFAULT_WAIT_STRATEGY, NO_RESERVATION_OBSERVER);
  }

  AsyncDiagnosticSink(
      FrameBatchConsumer consumer,
      DiagnosticCounters counters,
      Role role,
      int capacity,
      String threadName,
      AcceptanceObserver acceptanceObserver,
      WaitStrategy waitStrategy) {
    this(consumer, counters, role, capacity, threadName, acceptanceObserver, waitStrategy,
        NO_RESERVATION_OBSERVER);
  }

  AsyncDiagnosticSink(
      FrameBatchConsumer consumer,
      DiagnosticCounters counters,
      Role role,
      int capacity,
      String threadName,
      AcceptanceObserver acceptanceObserver,
      WaitStrategy waitStrategy,
      ReservationObserver reservationObserver) {
    if (consumer == null || counters == null || role == null || threadName == null
        || acceptanceObserver == null || waitStrategy == null || reservationObserver == null
        || (role == Role.LOCAL && capacity != DiagnosticConfig.OFFLINE_CAPTURE_LOCAL_QUEUE_CAPACITY)
        || (role == Role.NETWORK && capacity != DiagnosticConfig.OFFLINE_CAPTURE_NETWORK_QUEUE_CAPACITY)) {
      throw new IllegalArgumentException("invalid diagnostic sink configuration");
    }
    this.consumer = consumer;
    this.counters = counters;
    this.role = role;
    this.acceptanceObserver = acceptanceObserver;
    this.waitStrategy = waitStrategy;
    this.reservationObserver = reservationObserver;
    queue = new ArrayBlockingQueue<>(capacity);
    worker = new Thread(this::runWorker, threadName);
    worker.setDaemon(true);
    worker.start();
  }

  @Override
  public boolean offer(EncodedDiagnosticFrame frame) {
    if (frame == null) {
      return false;
    }
    if (!acceptanceLock.tryLock()) {
      addQueueDrop();
      return false;
    }
    try {
      if (!accepting) {
        return false;
      }
      acceptanceObserver.beforeQueueOffer();
      boolean accepted = queue.offer(frame);
      if (!accepted) {
        addQueueDrop();
      }
      return accepted;
    } finally {
      acceptanceLock.unlock();
    }
  }

  int capacity() {
    return queue.size() + queue.remainingCapacity();
  }

  long dropped() {
    DiagnosticCounters.Snapshot snapshot = counters.snapshot();
    return role == Role.LOCAL ? snapshot.localQueueDropped : snapshot.networkQueueDropped;
  }

  boolean awaitTermination(long timeout, TimeUnit unit) throws InterruptedException {
    return workerTerminated.await(timeout, unit);
  }

  boolean awaitTerminal(long timeout, TimeUnit unit) throws InterruptedException {
    return terminalPublished.await(timeout, unit);
  }

  @Override
  public void drainAndClose(long timeoutMillis) {
    if (timeoutMillis < 0L) {
      throw new IllegalArgumentException("negative diagnostic sink close timeout");
    }
    stopAccepting();
    worker.interrupt();

    long deadlineNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
    long graceMillis = timeoutMillis / 2L;
    if (!awaitWorker(graceMillis)) {
      beginTerminalCancellation();
      worker.interrupt();
    }
    awaitWorker(remainingMillis(deadlineNanos));
    beginTerminalCancellation();
  }

  @Override
  public boolean quiesceAndClose(long timeoutMillis) {
    if (timeoutMillis < 0L) {
      return false;
    }
    long deadlineNanos =
        System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
    if (!stopAcceptingUntil(deadlineNanos)) {
      return false;
    }
    worker.interrupt();
    if (role == Role.NETWORK) {
      if (remainingMillis(deadlineNanos) == 0L) {
        return false;
      }
      beginTerminalCancellation();
      worker.interrupt();
      return awaitWorker(remainingMillis(deadlineNanos))
          && awaitConsumerClose(remainingMillis(deadlineNanos))
          && consumerCloseSucceeded.get();
    }
    if (!awaitWorker(remainingMillis(deadlineNanos)) || gracefulFailure) {
      return false;
    }
    requestConsumerClose();
    return awaitConsumerClose(remainingMillis(deadlineNanos))
        && consumerCloseSucceeded.get();
  }

  private void stopAccepting() {
    acceptanceObserver.beforeClose();
    if (!acceptanceLock.tryLock()) {
      acceptanceObserver.closeWaitingForAcceptance();
      acceptanceLock.lock();
    }
    try {
      accepting = false;
      closing = true;
      acceptanceObserver.afterAcceptanceClosed();
    } finally {
      acceptanceLock.unlock();
    }
  }

  private boolean stopAcceptingUntil(long deadlineNanos) {
    accepting = false;
    acceptanceObserver.beforeClose();
    boolean locked = false;
    try {
      long remaining = remainingMillis(deadlineNanos);
      locked = acceptanceLock.tryLock(remaining, TimeUnit.MILLISECONDS);
      if (!locked) {
        acceptanceObserver.closeWaitingForAcceptance();
        return false;
      }
      accepting = false;
      closing = true;
      acceptanceObserver.afterAcceptanceClosed();
      return true;
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      return false;
    } finally {
      if (locked) {
        acceptanceLock.unlock();
      }
    }
  }

  private void runWorker() {
    List<EncodedDiagnosticFrame> batch = new ArrayList<>(BATCH_SIZE);
    long retryMillis = RETRY_INITIAL_MS;
    try {
      while (true) {
        if (isTerminal()) {
          return;
        }
        if (batch.isEmpty()) {
          if (closing && queue.isEmpty()) {
            return;
          }
          EncodedDiagnosticFrame first = poll(FLUSH_DEADLINE_MS);
          if (first == null) {
            continue;
          }
          batch.add(first);
          long deadlineNanos = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(FLUSH_DEADLINE_MS);
          while (batch.size() < BATCH_SIZE) {
            long remainingNanos = deadlineNanos - System.nanoTime();
            if (remainingNanos <= 0L) {
              break;
            }
            EncodedDiagnosticFrame next = pollNanos(remainingNanos);
            if (next == null) {
              break;
            }
            batch.add(next);
          }
        }
        if (isTerminal()) {
          return;
        }
        try {
          if (!reserveConsumerCall()) {
            return;
          }
          try {
            consumer.writeBatch(batch, counters.snapshot());
          } finally {
            releaseConsumerCall();
          }
          if (isTerminal()) {
            return;
          }
          batch.clear();
          retryMillis = RETRY_INITIAL_MS;
        } catch (IOException | RuntimeException failure) {
          addConsumerError();
          if (isTerminal()) {
            return;
          }
          if (closing) {
            gracefulFailure = true;
            batch.clear();
            return;
          }
          try {
            waitStrategy.await(retryMillis);
          } catch (InterruptedException interrupted) {
            if (closing) {
              continue;
            }
          }
          retryMillis = Math.min(RETRY_MAX_MS, retryMillis * 2L);
        }
      }
    } finally {
      workerTerminated.countDown();
    }
  }

  private EncodedDiagnosticFrame poll(long timeoutMillis) {
    try {
      return queue.poll(timeoutMillis, TimeUnit.MILLISECONDS);
    } catch (InterruptedException interrupted) {
      return null;
    }
  }

  private EncodedDiagnosticFrame pollNanos(long timeoutNanos) {
    try {
      return queue.poll(timeoutNanos, TimeUnit.NANOSECONDS);
    } catch (InterruptedException interrupted) {
      return null;
    }
  }

  private boolean awaitWorker(long timeoutMillis) {
    try {
      return workerTerminated.await(timeoutMillis, TimeUnit.MILLISECONDS);
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      return false;
    }
  }

  private boolean awaitConsumerClose(long timeoutMillis) {
    try {
      return consumerCloseCompleted.await(timeoutMillis, TimeUnit.MILLISECONDS);
    } catch (InterruptedException interrupted) {
      Thread.currentThread().interrupt();
      return false;
    }
  }

  private static long remainingMillis(long deadlineNanos) {
    long remainingNanos = deadlineNanos - System.nanoTime();
    return remainingNanos <= 0L ? 0L : TimeUnit.NANOSECONDS.toMillis(remainingNanos);
  }

  private void beginTerminalCancellation() {
    synchronized (consumerLifecycleLock) {
      if (!terminal) {
        terminal = true;
        terminalPublished.countDown();
      }
    }
    requestConsumerClose();
  }

  private boolean isTerminal() {
    synchronized (consumerLifecycleLock) {
      return terminal;
    }
  }

  private boolean reserveConsumerCall() {
    reservationObserver.beforeReservation();
    synchronized (consumerLifecycleLock) {
      if (terminal) {
        return false;
      }
      consumerCallActive = true;
      return true;
    }
  }

  private void releaseConsumerCall() {
    synchronized (consumerLifecycleLock) {
      consumerCallActive = false;
      consumerLifecycleLock.notifyAll();
    }
  }

  private void requestConsumerClose() {
    if (!consumerCloseStarted.compareAndSet(false, true)) {
      return;
    }
    // Close may cancel an in-flight write. It runs on a daemon, never on the caller thread; the
    // terminal reservation gate prevents all later consumer reuse, and the bounded worker wait
    // observes successful cancellation without allowing a blocked close to delay the caller.
    Thread closer = new Thread(() -> {
      boolean succeeded = false;
      try {
        consumer.close();
        succeeded = true;
      } catch (RuntimeException ignored) {
        // The extraction caller observes failure without exporting exception text.
      } finally {
        consumerCloseSucceeded.set(succeeded);
        consumerCloseCompleted.countDown();
      }
    }, worker.getName() + "-closer");
    closer.setDaemon(true);
    closer.start();
  }

  private void addQueueDrop() {
    if (role == Role.LOCAL) {
      counters.addLocalQueueDropped(1L);
    } else {
      counters.addNetworkQueueDropped(1L);
    }
  }

  private void addConsumerError() {
    if (role == Role.LOCAL) {
      counters.addStorageErrors(1L);
    } else {
      counters.addNetworkErrors(1L);
    }
  }
}

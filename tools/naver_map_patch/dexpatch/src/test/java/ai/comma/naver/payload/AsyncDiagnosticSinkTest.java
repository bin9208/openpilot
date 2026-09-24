package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;

final class AsyncDiagnosticSinkTest {
  @Test
  void closeCannotPassAnOfferThatObservedOpen() throws Exception {
    RecordingConsumer consumer = new RecordingConsumer();
    BlockingAcceptanceObserver acceptance = new BlockingAcceptanceObserver();
    AsyncDiagnosticSink sink = sink(
        consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.LOCAL, acceptance,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    AtomicBoolean accepted = new AtomicBoolean();
    Thread offer = new Thread(() -> accepted.set(sink.offer(frame(1))), "offer-race-test");
    AtomicBoolean closeReturned = new AtomicBoolean();
    Thread close = new Thread(() -> {
      sink.drainAndClose(500L);
      closeReturned.set(true);
    }, "close-race-test");
    try {
      offer.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      close.start();
      assertTrue(acceptance.closeWaitingForAcceptance.await(1, TimeUnit.SECONDS));
      acceptance.release.countDown();
      offer.join(1000L);
      close.join(1000L);
      assertTrue(accepted.get());
      assertTrue(consumer.received.await(1, TimeUnit.SECONDS));
      assertTrue(closeReturned.get());
      assertTrue(sink.awaitTermination(1, TimeUnit.SECONDS));
    } finally {
      acceptance.release.countDown();
      sink.drainAndClose(500L);
    }
  }

  @Test
  void acceptanceContentionDropsImmediatelyWithRoleSpecificCounter() throws Exception {
    assertAcceptanceContentionDrops(AsyncDiagnosticSink.Role.LOCAL);
    assertAcceptanceContentionDrops(AsyncDiagnosticSink.Role.NETWORK);
  }

  @Test
  void batchingAndRetryConstantsComeFromGeneratedProfile() {
    assertEquals(DiagnosticConfig.OFFLINE_CAPTURE_BATCH_SIZE, AsyncDiagnosticSink.BATCH_SIZE);
    assertEquals(
        DiagnosticConfig.OFFLINE_CAPTURE_FLUSH_INTERVAL_MS,
        AsyncDiagnosticSink.FLUSH_DEADLINE_MS);
    assertEquals(
        DiagnosticConfig.OFFLINE_CAPTURE_RETRY_INITIAL_MS,
        AsyncDiagnosticSink.RETRY_INITIAL_MS);
    assertEquals(
        DiagnosticConfig.OFFLINE_CAPTURE_RETRY_MAX_MS,
        AsyncDiagnosticSink.RETRY_MAX_MS);
  }

  @Test
  void terminalCancellationPreventsReservedConsumerCall() throws Exception {
    RecordingConsumer consumer = new RecordingConsumer();
    BlockingReservationObserver reservation = new BlockingReservationObserver();
    AsyncDiagnosticSink sink = sink(consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY,
        reservation);
    try {
      assertTrue(sink.offer(frame(1)));
      assertTrue(reservation.entered.await(1, TimeUnit.SECONDS));
      sink.drainAndClose(0L);
      assertTrue(sink.awaitTerminal(1, TimeUnit.SECONDS));
      reservation.release.countDown();
      assertTrue(sink.awaitTermination(1, TimeUnit.SECONDS));
      assertTrue(consumer.batches.isEmpty());
    } finally {
      reservation.release.countDown();
      sink.drainAndClose(0L);
    }
  }

  @Test
  void queuesUseFixedCapacitiesAndIndependentDropCounters() throws Exception {
    DiagnosticCounters counters = new DiagnosticCounters();
    BlockingConsumer localConsumer = new BlockingConsumer();
    BlockingConsumer networkConsumer = new BlockingConsumer();
    AsyncDiagnosticSink local = sink(
        localConsumer, counters, AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    AsyncDiagnosticSink network = sink(
        networkConsumer, counters, AsyncDiagnosticSink.Role.NETWORK,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    try {
      assertEquals(256, local.capacity());
      assertEquals(64, network.capacity());
      assertTrue(local.offer(frame(1)));
      assertTrue(network.offer(frame(2)));
      assertTrue(localConsumer.started.await(1, TimeUnit.SECONDS));
      assertTrue(networkConsumer.started.await(1, TimeUnit.SECONDS));
      for (int index = 0; index < 256; index++) assertTrue(local.offer(frame(10 + index)));
      for (int index = 0; index < 64; index++) assertTrue(network.offer(frame(300 + index)));
      assertFalse(local.offer(frame(999)));
      assertFalse(network.offer(frame(1000)));
      assertEquals(1L, counters.snapshot().localQueueDropped);
      assertEquals(1L, counters.snapshot().networkQueueDropped);
    } finally {
      localConsumer.release.countDown();
      networkConsumer.release.countDown();
      local.drainAndClose(500L);
      network.drainAndClose(500L);
    }
  }

  @Test
  void retainsFailedBatchAndUsesExponentialCappedBackoff() throws Exception {
    FailingThenRecordingConsumer consumer = new FailingThenRecordingConsumer(7);
    RecordingWaitStrategy waits = new RecordingWaitStrategy();
    AsyncDiagnosticSink sink = sink(
        consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, waits);
    try {
      assertTrue(sink.offer(frame(1)));
      assertTrue(consumer.received.await(1, TimeUnit.SECONDS));
      assertEquals(List.of(1000L, 2000L, 4000L, 8000L, 16000L, 30000L, 30000L), waits.values);
      assertEquals(8, consumer.attempts);
      assertEquals(1, consumer.frames.size());
    } finally {
      sink.drainAndClose(500L);
    }
  }

  @Test
  void consumerErrorsAreRoleSpecific() throws Exception {
    DiagnosticCounters counters = new DiagnosticCounters();
    FailingThenRecordingConsumer localConsumer = new FailingThenRecordingConsumer(1);
    FailingThenRecordingConsumer networkConsumer = new FailingThenRecordingConsumer(1);
    AsyncDiagnosticSink local = sink(localConsumer, counters, AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, new RecordingWaitStrategy());
    AsyncDiagnosticSink network = sink(networkConsumer, counters, AsyncDiagnosticSink.Role.NETWORK,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, new RecordingWaitStrategy());
    try {
      assertTrue(local.offer(frame(1)));
      assertTrue(network.offer(frame(2)));
      assertTrue(localConsumer.received.await(1, TimeUnit.SECONDS));
      assertTrue(networkConsumer.received.await(1, TimeUnit.SECONDS));
      assertEquals(1L, counters.snapshot().storageErrors);
      assertEquals(1L, counters.snapshot().networkErrors);
    } finally {
      local.drainAndClose(500L);
      network.drainAndClose(500L);
    }
  }

  @Test
  void batchesAtMostThirtyTwoAndFlushesPartialBatchByDeadline() throws Exception {
    RecordingConsumer consumer = new RecordingConsumer(33);
    AsyncDiagnosticSink sink = sink(consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    long started = System.nanoTime();
    try {
      for (int index = 0; index < 33; index++) assertTrue(sink.offer(frame(index + 1)));
      assertTrue(consumer.received.await(1, TimeUnit.SECONDS));
      assertTrue(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)
          <= AsyncDiagnosticSink.FLUSH_DEADLINE_MS + 250L);
      assertTrue(consumer.batches.stream().allMatch(batch -> batch.size() <= 32));
    } finally {
      sink.drainAndClose(500L);
    }
  }

  @Test
  void closeBoundsBlockedWriteAndBlockedConsumerCloseThenTerminatesWorker() throws Exception {
    CloseControlledConsumer consumer = new CloseControlledConsumer();
    AsyncDiagnosticSink sink = sink(consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.NETWORK,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    AtomicBoolean returned = new AtomicBoolean();
    AtomicLong elapsedMillis = new AtomicLong();
    Thread close = new Thread(() -> {
      long started = System.nanoTime();
      sink.drainAndClose(200L);
      elapsedMillis.set(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
      returned.set(true);
    }, "bounded-close-test");
    try {
      assertTrue(sink.offer(frame(1)));
      assertTrue(consumer.writeStarted.await(1, TimeUnit.SECONDS));
      close.start();
      assertTrue(consumer.closeStarted.await(1, TimeUnit.SECONDS));
      assertTrue(consumer.writeFinished.await(1, TimeUnit.SECONDS));
      close.join(1000L);
      assertTrue(returned.get());
      assertTrue(elapsedMillis.get() < 500L);
      assertTrue(sink.awaitTermination(1, TimeUnit.SECONDS));
      assertEquals(1, consumer.writeCalls);
      consumer.allowCloseReturn.countDown();
      assertTrue(consumer.closeFinished.await(1, TimeUnit.SECONDS));
    } finally {
      consumer.allowCloseReturn.countDown();
      sink.drainAndClose(200L);
    }
  }

  @Test
  void closeInterruptsBackoffAndTerminatesWorker() throws Exception {
    FailingThenRecordingConsumer consumer = new FailingThenRecordingConsumer(Integer.MAX_VALUE);
    BlockingWaitStrategy waits = new BlockingWaitStrategy();
    AsyncDiagnosticSink sink = sink(consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER, waits);
    try {
      assertTrue(sink.offer(frame(1)));
      assertTrue(waits.entered.await(1, TimeUnit.SECONDS));
      sink.drainAndClose(500L);
      assertTrue(sink.awaitTermination(1, TimeUnit.SECONDS));
    } finally {
      waits.release.countDown();
      sink.drainAndClose(500L);
    }
  }

  @Test
  void extractionQuiesceWaitsForInflightBatchAndConsumerCloseCompletion()
      throws Exception {
    CloseControlledConsumer consumer = new CloseControlledConsumer();
    AsyncDiagnosticSink sink = sink(
        consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    AtomicBoolean result = new AtomicBoolean();
    CountDownLatch returned = new CountDownLatch(1);
    Thread quiesce = new Thread(() -> {
      result.set(sink.quiesceAndClose(2_000L));
      returned.countDown();
    }, "extraction-quiesce-test");
    try {
      assertTrue(sink.offer(frame(1)));
      assertTrue(consumer.writeStarted.await(1, TimeUnit.SECONDS));
      quiesce.start();
      assertFalse(returned.await(100, TimeUnit.MILLISECONDS));
      consumer.allowWriteReturn.countDown();
      assertTrue(consumer.closeStarted.await(1, TimeUnit.SECONDS));
      assertFalse(returned.await(100, TimeUnit.MILLISECONDS));
      consumer.allowCloseReturn.countDown();
      assertTrue(returned.await(1, TimeUnit.SECONDS));
      assertTrue(result.get());
      assertTrue(consumer.writeFinished.await(1, TimeUnit.SECONDS));
      assertTrue(consumer.closeFinished.await(1, TimeUnit.SECONDS));
    } finally {
      consumer.allowWriteReturn.countDown();
      consumer.allowCloseReturn.countDown();
      sink.drainAndClose(200L);
    }
  }

  @Test
  void networkExtractionQuiesceWaitsForWorkerAndConsumerCloseCompletion()
      throws Exception {
    CloseControlledConsumer consumer = new CloseControlledConsumer();
    AsyncDiagnosticSink sink = sink(
        consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.NETWORK,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    AtomicBoolean result = new AtomicBoolean();
    CountDownLatch returned = new CountDownLatch(1);
    Thread quiesce = new Thread(() -> {
      result.set(sink.quiesceAndClose(2_000L));
      returned.countDown();
    }, "network-extraction-quiesce-test");
    try {
      assertTrue(sink.offer(frame(1)));
      assertTrue(consumer.writeStarted.await(1, TimeUnit.SECONDS));
      quiesce.start();
      assertTrue(consumer.closeStarted.await(1, TimeUnit.SECONDS));
      assertFalse(returned.await(100, TimeUnit.MILLISECONDS));
      consumer.allowWriteReturn.countDown();
      consumer.allowCloseReturn.countDown();
      assertTrue(returned.await(1, TimeUnit.SECONDS));
      assertTrue(result.get());
      assertTrue(consumer.writeFinished.await(1, TimeUnit.SECONDS));
      assertTrue(consumer.closeFinished.await(1, TimeUnit.SECONDS));
      assertTrue(sink.awaitTermination(1, TimeUnit.SECONDS));
    } finally {
      consumer.allowWriteReturn.countDown();
      consumer.allowCloseReturn.countDown();
      sink.drainAndClose(200L);
    }
  }

  @Test
  void extractionQuiesceFailsClosedWhenConsumerCloseMissesDeadline()
      throws Exception {
    CloseControlledConsumer consumer = new CloseControlledConsumer();
    AsyncDiagnosticSink sink = sink(
        consumer, new DiagnosticCounters(), AsyncDiagnosticSink.Role.LOCAL,
        AsyncDiagnosticSink.NO_ACCEPTANCE_OBSERVER,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    try {
      assertTrue(sink.offer(frame(1)));
      assertTrue(consumer.writeStarted.await(1, TimeUnit.SECONDS));
      consumer.allowWriteReturn.countDown();
      assertFalse(sink.quiesceAndClose(100L));
      assertTrue(consumer.closeStarted.await(1, TimeUnit.SECONDS));
      assertFalse(sink.offer(frame(2)));
    } finally {
      consumer.allowCloseReturn.countDown();
      sink.drainAndClose(200L);
    }
  }

  @Test
  void extractionQuiesceTimeoutAlsoBoundsAcceptanceFreeze() throws Exception {
    BlockingAcceptanceObserver acceptance = new BlockingAcceptanceObserver();
    AsyncDiagnosticSink sink = sink(
        new RecordingConsumer(), new DiagnosticCounters(),
        AsyncDiagnosticSink.Role.LOCAL, acceptance,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    Thread offer = new Thread(() -> sink.offer(frame(1)), "held-acceptance-test");
    AtomicBoolean result = new AtomicBoolean(true);
    CountDownLatch returned = new CountDownLatch(1);
    Thread quiesce = new Thread(() -> {
      result.set(sink.quiesceAndClose(100L));
      returned.countDown();
    }, "bounded-acceptance-quiesce-test");
    try {
      offer.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      quiesce.start();
      assertTrue(returned.await(500, TimeUnit.MILLISECONDS));
      assertFalse(result.get());
      acceptance.release.countDown();
      offer.join(1_000L);
      assertFalse(sink.offer(frame(2)));
      assertFalse(acceptance.acceptanceClosed.await(100, TimeUnit.MILLISECONDS));
    } finally {
      acceptance.release.countDown();
      offer.join(1_000L);
      sink.drainAndClose(500L);
    }
  }

  @Test
  void networkExtractionQuiesceBoundsAcceptanceAndFailsClosed() throws Exception {
    BlockingAcceptanceObserver acceptance = new BlockingAcceptanceObserver();
    AsyncDiagnosticSink sink = sink(
        new RecordingConsumer(), new DiagnosticCounters(),
        AsyncDiagnosticSink.Role.NETWORK, acceptance,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    Thread offer = new Thread(() -> sink.offer(frame(1)), "held-network-acceptance-test");
    AtomicBoolean result = new AtomicBoolean(true);
    AtomicLong elapsedMillis = new AtomicLong();
    CountDownLatch returned = new CountDownLatch(1);
    Thread quiesce = new Thread(() -> {
      long started = System.nanoTime();
      result.set(sink.quiesceAndClose(100L));
      elapsedMillis.set(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
      returned.countDown();
    }, "bounded-network-sink-quiesce-test");
    try {
      offer.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      quiesce.start();
      assertTrue(returned.await(500, TimeUnit.MILLISECONDS));
      assertFalse(result.get());
      assertTrue(elapsedMillis.get() < 500L);
      acceptance.release.countDown();
      offer.join(1_000L);
      assertFalse(sink.offer(frame(2)));
      assertFalse(acceptance.acceptanceClosed.await(100, TimeUnit.MILLISECONDS));
    } finally {
      acceptance.release.countDown();
      offer.join(1_000L);
      quiesce.join(1_000L);
      sink.drainAndClose(500L);
    }
  }

  private static AsyncDiagnosticSink sink(
      FrameBatchConsumer consumer,
      DiagnosticCounters counters,
      AsyncDiagnosticSink.Role role,
      AsyncDiagnosticSink.AcceptanceObserver observer,
      AsyncDiagnosticSink.WaitStrategy waitStrategy) {
    return sink(consumer, counters, role, observer, waitStrategy,
        AsyncDiagnosticSink.NO_RESERVATION_OBSERVER);
  }

  private static void assertAcceptanceContentionDrops(AsyncDiagnosticSink.Role role)
      throws Exception {
    DiagnosticCounters counters = new DiagnosticCounters();
    BlockingAcceptanceObserver acceptance = new BlockingAcceptanceObserver();
    AsyncDiagnosticSink sink = sink(
        new RecordingConsumer(), counters, role, acceptance,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
    AtomicBoolean firstAccepted = new AtomicBoolean();
    AtomicBoolean secondAccepted = new AtomicBoolean(true);
    CountDownLatch secondReturned = new CountDownLatch(1);
    Thread first = new Thread(
        () -> firstAccepted.set(sink.offer(frame(1))), "acceptance-owner-test");
    Thread second = new Thread(() -> {
      secondAccepted.set(sink.offer(frame(2)));
      secondReturned.countDown();
    }, "acceptance-contender-test");
    try {
      first.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      second.start();
      assertTrue(secondReturned.await(250, TimeUnit.MILLISECONDS));
      assertFalse(secondAccepted.get());
      assertEquals(
          role == AsyncDiagnosticSink.Role.LOCAL ? 1L : 0L,
          counters.snapshot().localQueueDropped);
      assertEquals(
          role == AsyncDiagnosticSink.Role.NETWORK ? 1L : 0L,
          counters.snapshot().networkQueueDropped);
      acceptance.release.countDown();
      first.join(1000L);
      assertTrue(firstAccepted.get());
    } finally {
      acceptance.release.countDown();
      sink.drainAndClose(500L);
    }
  }

  private static AsyncDiagnosticSink sink(
      FrameBatchConsumer consumer,
      DiagnosticCounters counters,
      AsyncDiagnosticSink.Role role,
      AsyncDiagnosticSink.AcceptanceObserver observer,
      AsyncDiagnosticSink.WaitStrategy waitStrategy,
      AsyncDiagnosticSink.ReservationObserver reservationObserver) {
    int capacity = role == AsyncDiagnosticSink.Role.LOCAL ? 256 : 64;
    return new AsyncDiagnosticSink(consumer, counters, role, capacity, "async-sink-test", observer,
        waitStrategy, reservationObserver);
  }

  private static EncodedDiagnosticFrame frame(long sequence) {
    return new EncodedDiagnosticFrame("status", sequence, sequence, 0L, "{}");
  }

  private static final class BlockingAcceptanceObserver
      implements AsyncDiagnosticSink.AcceptanceObserver {
    final CountDownLatch entered = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);
    final CountDownLatch closeWaitingForAcceptance = new CountDownLatch(1);
    final CountDownLatch acceptanceClosed = new CountDownLatch(1);

    @Override
    public void beforeQueueOffer() {
      entered.countDown();
      try {
        release.await();
      } catch (InterruptedException error) {
        Thread.currentThread().interrupt();
      }
    }

    @Override
    public void closeWaitingForAcceptance() {
      closeWaitingForAcceptance.countDown();
    }

    @Override
    public void afterAcceptanceClosed() {
      acceptanceClosed.countDown();
    }
  }

  private static final class BlockingReservationObserver
      implements AsyncDiagnosticSink.ReservationObserver {
    final CountDownLatch entered = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);

    @Override
    public void beforeReservation() {
      entered.countDown();
      boolean interrupted = false;
      while (release.getCount() != 0L) {
        try {
          release.await();
        } catch (InterruptedException error) {
          interrupted = true;
        }
      }
      if (interrupted) {
        Thread.currentThread().interrupt();
      }
    }
  }

  private static final class RecordingWaitStrategy implements AsyncDiagnosticSink.WaitStrategy {
    final CopyOnWriteArrayList<Long> values = new CopyOnWriteArrayList<>();

    @Override
    public void await(long millis) {
      values.add(millis);
    }
  }

  private static final class BlockingWaitStrategy implements AsyncDiagnosticSink.WaitStrategy {
    final CountDownLatch entered = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);

    @Override
    public void await(long millis) throws InterruptedException {
      entered.countDown();
      release.await();
    }
  }

  private static final class BlockingConsumer implements FrameBatchConsumer {
    final CountDownLatch started = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);

    @Override
    public void writeBatch(List<EncodedDiagnosticFrame> frames, DiagnosticCounters.Snapshot counters)
        throws IOException {
      started.countDown();
      try {
        release.await();
      } catch (InterruptedException error) {
        Thread.currentThread().interrupt();
        throw new IOException(error);
      }
    }

    @Override
    public void close() {
      release.countDown();
    }
  }

  private static final class RecordingConsumer implements FrameBatchConsumer {
    final CopyOnWriteArrayList<List<EncodedDiagnosticFrame>> batches = new CopyOnWriteArrayList<>();
    final CountDownLatch received = new CountDownLatch(1);
    final int expectedFrames;

    RecordingConsumer() {
      this(1);
    }

    RecordingConsumer(int expectedFrames) {
      this.expectedFrames = expectedFrames;
    }

    @Override
    public void writeBatch(List<EncodedDiagnosticFrame> batch, DiagnosticCounters.Snapshot counters) {
      batches.add(new ArrayList<>(batch));
      if (batches.stream().mapToInt(List::size).sum() >= expectedFrames) received.countDown();
    }

    @Override
    public void close() {
    }
  }

  private static final class FailingThenRecordingConsumer implements FrameBatchConsumer {
    final CountDownLatch received = new CountDownLatch(1);
    final CopyOnWriteArrayList<EncodedDiagnosticFrame> frames = new CopyOnWriteArrayList<>();
    int failuresRemaining;
    int attempts;

    FailingThenRecordingConsumer(int failures) {
      failuresRemaining = failures;
    }

    @Override
    public synchronized void writeBatch(
        List<EncodedDiagnosticFrame> batch, DiagnosticCounters.Snapshot counters) throws IOException {
      attempts++;
      if (failuresRemaining-- > 0) throw new IOException("expected failure");
      frames.addAll(batch);
      received.countDown();
    }

    @Override
    public void close() {
    }
  }

  private static final class CloseControlledConsumer implements FrameBatchConsumer {
    final CountDownLatch writeStarted = new CountDownLatch(1);
    final CountDownLatch writeFinished = new CountDownLatch(1);
    final CountDownLatch closeStarted = new CountDownLatch(1);
    final CountDownLatch allowWriteReturn = new CountDownLatch(1);
    final CountDownLatch allowCloseReturn = new CountDownLatch(1);
    final CountDownLatch closeFinished = new CountDownLatch(1);
    int writeCalls;

    @Override
    public void writeBatch(List<EncodedDiagnosticFrame> batch, DiagnosticCounters.Snapshot counters) {
      writeCalls++;
      writeStarted.countDown();
      try {
        allowWriteReturn.await();
      } catch (InterruptedException error) {
        Thread.currentThread().interrupt();
      } finally {
        writeFinished.countDown();
      }
    }

    @Override
    public void close() {
      closeStarted.countDown();
      allowWriteReturn.countDown();
      try {
        allowCloseReturn.await();
      } catch (InterruptedException error) {
        Thread.currentThread().interrupt();
      } finally {
        closeFinished.countDown();
      }
    }
  }
}

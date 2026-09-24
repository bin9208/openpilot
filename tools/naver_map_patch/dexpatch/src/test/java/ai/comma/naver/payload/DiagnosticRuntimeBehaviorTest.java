package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketAddress;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Pattern;
import org.junit.jupiter.api.Test;

final class DiagnosticRuntimeBehaviorTest {
  enum UnicodeValue {
    안내
  }

  @Test
  void closeCannotPassAnEnqueueThatObservedOpen() throws Exception {
    BlockingAcceptanceObserver acceptance = new BlockingAcceptanceObserver();
    RecordingSink local = new RecordingSink();
    RecordingSink network = new RecordingSink();
    DiagnosticRuntime runtime = runtime(local, network, acceptance);
    Thread enqueue = new Thread(() -> runtime.enqueue("status", Boolean.TRUE), "enqueue-race-test");
    CountDownLatch closeReturned = new CountDownLatch(1);
    Thread close = new Thread(() -> {
      runtime.close();
      closeReturned.countDown();
    }, "runtime-close-race-test");
    try {
      enqueue.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      close.start();
      assertTrue(acceptance.closeWaitingForAcceptance.await(1, TimeUnit.SECONDS));
      acceptance.release.countDown();
      enqueue.join(1000L);
      close.join(1000L);
      assertTrue(local.offered.await(1, TimeUnit.SECONDS));
      assertTrue(network.offered.await(1, TimeUnit.SECONDS));
      assertEquals(0L, closeReturned.getCount());
    } finally {
      acceptance.release.countDown();
      runtime.close();
    }
  }

  @Test
  void enqueueContentionDropsImmediatelyWithoutParkingHook() throws Exception {
    DiagnosticCounters counters = new DiagnosticCounters();
    BlockingAcceptanceObserver acceptance = new BlockingAcceptanceObserver();
    RecordingSink local = new RecordingSink();
    RecordingSink network = new RecordingSink();
    DiagnosticRuntime runtime = runtime(local, network, counters, acceptance);
    CountDownLatch contenderReturned = new CountDownLatch(1);
    Thread owner = new Thread(
        () -> runtime.enqueue("status", Boolean.TRUE), "enqueue-owner-test");
    Thread contender = new Thread(() -> {
      runtime.enqueue("route", Boolean.TRUE);
      contenderReturned.countDown();
    }, "enqueue-contender-test");
    try {
      owner.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      contender.start();
      assertTrue(contenderReturned.await(250, TimeUnit.MILLISECONDS));
      assertEquals(1L, counters.snapshot().objectQueueDropped);
      acceptance.release.countDown();
      owner.join(1000L);
      assertTrue(local.offered.await(1, TimeUnit.SECONDS));
      assertTrue(network.offered.await(1, TimeUnit.SECONDS));
    } finally {
      acceptance.release.countDown();
      runtime.close();
    }
  }

  @Test
  void productionRunIdsAreUniqueCanonicalLowercaseUuids() {
    String first = DiagnosticRuntime.newRunId();
    String second = DiagnosticRuntime.newRunId();

    assertFalse(first.equals(second));
    assertTrue(Pattern.matches(
        "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        first));
    assertTrue(Pattern.matches(
        "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        second));
  }

  @Test
  void mappingRejectionIncrementsSharedRejectedFrameCounter() throws Exception {
    DiagnosticCounters counters = new DiagnosticCounters();
    RecordingSink local = new RecordingSink();
    RecordingSink network = new RecordingSink();
    DiagnosticRuntime runtime = runtime(
        local, network, counters, DiagnosticRuntime.NO_ACCEPTANCE_OBSERVER);
    try {
      runtime.enqueue("status", UnicodeValue.안내);
      long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(1);
      while (counters.snapshot().rejectedFrames == 0L && System.nanoTime() < deadline) {
        Thread.yield();
      }
      assertEquals(1L, counters.snapshot().rejectedFrames);
      assertEquals(1L, local.offered.getCount());
      assertEquals(1L, network.offered.getCount());
    } finally {
      runtime.close();
    }
  }

  @Test
  void closeWaitsForAcceptedMapperWorkBeforeClosingSinks() throws Exception {
    BlockingOfferSink local = new BlockingOfferSink();
    RecordingSink network = new RecordingSink();
    CloseEntryObserver closeObserver = new CloseEntryObserver();
    ControlledMapperJoinStrategy joinStrategy = new ControlledMapperJoinStrategy(local.offerFinished);
    DiagnosticRuntime runtime = runtime(local, network, closeObserver, joinStrategy);
    CountDownLatch closeReturned = new CountDownLatch(1);
    Thread close = new Thread(() -> {
      runtime.close();
      closeReturned.countDown();
    }, "delayed-mapper-close-test");
    try {
      runtime.enqueue("status", Boolean.TRUE);
      assertTrue(local.offerStarted.await(1, TimeUnit.SECONDS));
      close.start();
      assertTrue(closeObserver.acceptanceClosed.await(1, TimeUnit.SECONDS));
      assertTrue(joinStrategy.waitingForMapper.await(1, TimeUnit.SECONDS));
      assertEquals(1L, local.closed.getCount());
      local.release.countDown();
      assertTrue(closeReturned.await(1, TimeUnit.SECONDS));
      assertTrue(local.closed.await(1, TimeUnit.SECONDS));
      assertTrue(network.closed.await(1, TimeUnit.SECONDS));
    } finally {
      local.release.countDown();
      runtime.close();
    }
  }

  @Test
  void blockedNetworkCannotBlockOrDropAcceptedLocalFrames() throws Exception {
    RecordingConsumer local = new RecordingConsumer();
    BlockingConsumer network = new BlockingConsumer();
    AsyncDiagnosticSink localSink = sink(local, 256, AsyncDiagnosticSink.Role.LOCAL);
    AsyncDiagnosticSink networkSink = sink(network, 64, AsyncDiagnosticSink.Role.NETWORK);
    DiagnosticRuntime runtime = runtime(localSink, networkSink);
    try {
      runtime.enqueue("status", Boolean.TRUE);
      assertTrue(local.received.await(1, TimeUnit.SECONDS));
      assertEquals(1, local.frames.size());
      assertEquals(0L, localSink.dropped());
    } finally {
      network.release.countDown();
      runtime.close();
    }
  }

  @Test
  void mapperOffersLocalBeforeNetwork() throws Exception {
    List<String> order = new CopyOnWriteArrayList<>();
    DiagnosticRuntime runtime = runtime(
        recordingSink("local", order), recordingSink("network", order));
    try {
      runtime.enqueue("status", Boolean.TRUE);
      awaitSize(order, 2);
      assertEquals(List.of("local", "network"), order);
    } finally {
      runtime.close();
    }
  }

  @Test
  void extractionQuiesceFreezesAcceptanceDrainsMapperAndIsIdempotent()
      throws Exception {
    RecordingSink local = new RecordingSink();
    RecordingSink network = new RecordingSink();
    DiagnosticRuntime runtime = runtime(local, network);

    runtime.enqueue("status", Boolean.TRUE);
    assertTrue(runtime.quiesceForExtraction(1_000L));
    assertTrue(local.offered.await(1, TimeUnit.SECONDS));
    assertEquals(1, local.quiesceCalls);
    assertEquals(1, network.quiesceCalls);
    assertTrue(runtime.quiesceForExtraction(1_000L));
    assertEquals(1, local.quiesceCalls);
    assertEquals(1, network.quiesceCalls);

    runtime.enqueue("route", Boolean.TRUE);
    Thread.sleep(50L);
    assertEquals(1, local.frames.size());
  }

  @Test
  void extractionPublishesAfterBothSinksCloseAndPublishesOnlyOnce() {
    RecordingSink local = new RecordingSink();
    RecordingSink network = new RecordingSink();
    AtomicInteger publishCalls = new AtomicInteger();
    DiagnosticRuntime runtime = runtime(local, network, deadlineNanos -> {
      assertEquals(0L, local.closed.getCount());
      assertEquals(0L, network.closed.getCount());
      assertTrue(deadlineNanos > System.nanoTime());
      publishCalls.incrementAndGet();
      return true;
    });

    assertTrue(runtime.quiesceForExtraction(1_000L));
    assertTrue(runtime.quiesceForExtraction(1_000L));

    assertEquals(1, publishCalls.get());
    assertEquals(1, local.quiesceCalls);
    assertEquals(1, network.quiesceCalls);
  }

  @Test
  void extractionPublisherFailureIsTerminalAndNeverRetries() {
    RecordingSink local = new RecordingSink();
    RecordingSink network = new RecordingSink();
    AtomicInteger publishCalls = new AtomicInteger();
    DiagnosticRuntime runtime = runtime(local, network, deadlineNanos -> {
      publishCalls.incrementAndGet();
      return false;
    });

    assertFalse(runtime.quiesceForExtraction(1_000L));
    assertFalse(runtime.quiesceForExtraction(1_000L));

    assertEquals(1, publishCalls.get());
    assertEquals(1, local.quiesceCalls);
    assertEquals(1, network.quiesceCalls);
  }

  @Test
  void failedSinkQuiesceNeverPublishes() {
    RecordingSink local = new RecordingSink();
    RecordingSink network = new RecordingSink();
    network.quiesceResult = false;
    AtomicInteger publishCalls = new AtomicInteger();
    DiagnosticRuntime runtime = runtime(local, network, deadlineNanos -> {
      publishCalls.incrementAndGet();
      return true;
    });

    assertFalse(runtime.quiesceForExtraction(1_000L));

    assertEquals(0, publishCalls.get());
    assertEquals(1, local.quiesceCalls);
    assertEquals(1, network.quiesceCalls);
  }

  @Test
  void generalCloseNeverPublishes() {
    AtomicInteger publishCalls = new AtomicInteger();
    DiagnosticRuntime runtime = runtime(
        new RecordingSink(), new RecordingSink(), deadlineNanos -> {
          publishCalls.incrementAndGet();
          return true;
        });

    runtime.close();
    runtime.close();

    assertEquals(0, publishCalls.get());
  }

  @Test
  void extractionQuiesceFailureIsTerminalAndCannotLaterOpen() {
    RecordingSink local = new RecordingSink();
    local.quiesceResult = false;
    RecordingSink network = new RecordingSink();
    DiagnosticRuntime runtime = runtime(local, network);

    assertFalse(runtime.quiesceForExtraction(100L));
    local.quiesceResult = true;
    assertFalse(runtime.quiesceForExtraction(1_000L));
    assertEquals(1, local.quiesceCalls);
  }

  @Test
  void concurrentExtractionWaitIsBoundedByEachCallersDeadline() throws Exception {
    BlockingQuiesceSink local = new BlockingQuiesceSink();
    RecordingSink network = new RecordingSink();
    DiagnosticRuntime runtime = runtime(local, network);
    AtomicBoolean firstResult = new AtomicBoolean();
    AtomicBoolean secondResult = new AtomicBoolean(true);
    AtomicLong secondElapsedMillis = new AtomicLong();
    CountDownLatch firstReturned = new CountDownLatch(1);
    CountDownLatch secondReturned = new CountDownLatch(1);
    Thread first = new Thread(() -> {
      firstResult.set(runtime.quiesceForExtraction(2_000L));
      firstReturned.countDown();
    }, "first-extraction-quiesce-test");
    Thread second = new Thread(() -> {
      long started = System.nanoTime();
      secondResult.set(runtime.quiesceForExtraction(100L));
      secondElapsedMillis.set(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
      secondReturned.countDown();
    }, "second-extraction-quiesce-test");
    try {
      first.start();
      assertTrue(local.quiesceStarted.await(1, TimeUnit.SECONDS));
      second.start();
      assertTrue(secondReturned.await(500, TimeUnit.MILLISECONDS));
      assertFalse(secondResult.get());
      assertTrue(secondElapsedMillis.get() < 500L);
      assertEquals(1, local.quiesceCalls);

      local.release.countDown();
      assertTrue(firstReturned.await(1, TimeUnit.SECONDS));
      assertTrue(firstResult.get());
      assertTrue(runtime.quiesceForExtraction(1_000L));
      assertEquals(1, local.quiesceCalls);
      assertEquals(1, network.quiesceCalls);
    } finally {
      local.release.countDown();
      first.join(1_000L);
      second.join(1_000L);
      runtime.close();
    }
  }

  @Test
  void extractionQuiesceBoundsNetworkAcceptanceAndStaysFailedClosed() throws Exception {
    BlockingSinkAcceptanceObserver acceptance = new BlockingSinkAcceptanceObserver();
    AsyncDiagnosticSink network = sink(
        new RecordingConsumer(),
        DiagnosticConfig.OFFLINE_CAPTURE_NETWORK_QUEUE_CAPACITY,
        AsyncDiagnosticSink.Role.NETWORK,
        acceptance);
    RecordingSink local = new RecordingSink();
    DiagnosticRuntime runtime = runtime(local, network);
    Thread heldOffer = new Thread(
        () -> network.offer(frame(1L)), "held-network-acceptance-test");
    AtomicBoolean result = new AtomicBoolean(true);
    AtomicLong elapsedMillis = new AtomicLong();
    CountDownLatch returned = new CountDownLatch(1);
    Thread quiesce = new Thread(() -> {
      long started = System.nanoTime();
      result.set(runtime.quiesceForExtraction(100L));
      elapsedMillis.set(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
      returned.countDown();
    }, "bounded-network-extraction-test");
    try {
      heldOffer.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      quiesce.start();
      assertTrue(returned.await(500, TimeUnit.MILLISECONDS));
      assertFalse(result.get());
      assertTrue(elapsedMillis.get() < 500L);
      assertFalse(runtime.quiesceForExtraction(1_000L));
      assertEquals(1, local.quiesceCalls);

      acceptance.release.countDown();
      heldOffer.join(1_000L);
      assertFalse(network.offer(frame(2L)));
      assertFalse(acceptance.acceptanceClosed.await(100, TimeUnit.MILLISECONDS));
    } finally {
      acceptance.release.countDown();
      heldOffer.join(1_000L);
      quiesce.join(1_000L);
      network.drainAndClose(500L);
      runtime.close();
    }
  }

  @Test
  void extractionQuiesceTimeoutBoundsAcceptanceFreeze() throws Exception {
    BlockingAcceptanceObserver acceptance = new BlockingAcceptanceObserver();
    RecordingSink local = new RecordingSink();
    DiagnosticRuntime runtime = runtime(local, new RecordingSink(), acceptance);
    Thread enqueue = new Thread(
        () -> runtime.enqueue("status", Boolean.TRUE),
        "held-runtime-acceptance-test");
    AtomicBoolean result = new AtomicBoolean(true);
    CountDownLatch returned = new CountDownLatch(1);
    Thread quiesce = new Thread(() -> {
      result.set(runtime.quiesceForExtraction(100L));
      returned.countDown();
    }, "bounded-runtime-quiesce-test");
    try {
      enqueue.start();
      assertTrue(acceptance.entered.await(1, TimeUnit.SECONDS));
      quiesce.start();
      assertTrue(returned.await(500, TimeUnit.MILLISECONDS));
      assertFalse(result.get());
      acceptance.release.countDown();
      enqueue.join(1_000L);
      Thread.sleep(100L);
      int framesBeforeRejectedEnqueue = local.frames.size();
      runtime.enqueue("route", Boolean.TRUE);
      Thread.sleep(100L);
      assertEquals(framesBeforeRejectedEnqueue, local.frames.size());
    } finally {
      acceptance.release.countDown();
      enqueue.join(1_000L);
      runtime.close();
    }
  }

  @Test
  void transportRejectsNonLoopbackAndUnexpectedFixedParameters() {
    assertThrows(IllegalArgumentException.class,
        () -> new PersistentNavigationTransport("192.0.2.1", 7712, 100));
    assertThrows(IllegalArgumentException.class,
        () -> new PersistentNavigationTransport("127.0.0.1", 7713, 100));
    assertThrows(IllegalArgumentException.class,
        () -> new PersistentNavigationTransport("127.0.0.1", 7712, 101));
    assertDoesNotThrow(() -> new PersistentNavigationTransport("127.0.0.1", 7712, 100));
  }

  @Test
  void terminalTransportCloseUnblocksSendAndRejectsReconnect() throws Exception {
    BlockingSocket socket = new BlockingSocket();
    AtomicBoolean sendFinished = new AtomicBoolean();
    PersistentNavigationTransport transport = new PersistentNavigationTransport(
        "127.0.0.1", 7712, 100, () -> socket);
    Thread send = new Thread(() -> {
      try {
        transport.send("frame");
      } catch (IOException ignored) {
        // Terminal socket close is the expected release path.
      } finally {
        sendFinished.set(true);
      }
    }, "terminal-send-test");
    send.start();
    assertTrue(socket.writeStarted.await(1, TimeUnit.SECONDS));
    transport.close();
    send.join(1000L);
    assertTrue(sendFinished.get());
    assertThrows(IOException.class, () -> transport.send("after-close"));
    assertEquals(1, socket.connects);
  }

  @Test
  void failedPreTerminalSendDisconnectsAndLaterFrameReconnects() throws Exception {
    FailingSocket failed = new FailingSocket();
    SuccessfulSocket succeeding = new SuccessfulSocket();
    AtomicBoolean first = new AtomicBoolean(true);
    PersistentNavigationTransport transport = new PersistentNavigationTransport(
        "127.0.0.1", 7712, 100, () -> first.getAndSet(false) ? failed : succeeding);
    assertThrows(IOException.class, () -> transport.send("first"));
    assertDoesNotThrow(() -> transport.send("second"));
    assertEquals(1, failed.connects);
    assertEquals(1, succeeding.connects);
    transport.close();
  }

  private static AsyncDiagnosticSink sink(
      FrameBatchConsumer consumer, int capacity, AsyncDiagnosticSink.Role role) {
    return new AsyncDiagnosticSink(
        consumer, new DiagnosticCounters(), role, capacity, "diagnostic-runtime-test");
  }

  private static AsyncDiagnosticSink sink(
      FrameBatchConsumer consumer,
      int capacity,
      AsyncDiagnosticSink.Role role,
      AsyncDiagnosticSink.AcceptanceObserver observer) {
    return new AsyncDiagnosticSink(
        consumer,
        new DiagnosticCounters(),
        role,
        capacity,
        "diagnostic-runtime-test",
        observer,
        AsyncDiagnosticSink.DEFAULT_WAIT_STRATEGY);
  }

  private static EncodedDiagnosticFrame frame(long sequence) {
    return new EncodedDiagnosticFrame("status", sequence, sequence, 0L, "{}");
  }

  private static DiagnosticRuntime runtime(
      DiagnosticFrameSink localSink, DiagnosticFrameSink networkSink) {
    return new DiagnosticRuntime(
        new BoundedMappingDiagnostics(4096), localSink, networkSink, 16, "diagnostic-runtime-test");
  }

  private static DiagnosticRuntime runtime(
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      DiagnosticRuntime.ExtractionPublisher extractionPublisher) {
    return new DiagnosticRuntime(
        new BoundedMappingDiagnostics(4096),
        new DiagnosticCounters(),
        localSink,
        networkSink,
        16,
        "diagnostic-runtime-test",
        DiagnosticRuntime.NO_ACCEPTANCE_OBSERVER,
        DiagnosticRuntime.DEFAULT_MAPPER_JOIN_STRATEGY,
        extractionPublisher);
  }

  private static DiagnosticRuntime runtime(
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      DiagnosticCounters counters,
      DiagnosticRuntime.AcceptanceObserver observer) {
    return new DiagnosticRuntime(
        new BoundedMappingDiagnostics(4096), counters, localSink, networkSink, 16,
        "diagnostic-runtime-test", observer, DiagnosticRuntime.DEFAULT_MAPPER_JOIN_STRATEGY);
  }

  private static DiagnosticRuntime runtime(
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      DiagnosticRuntime.AcceptanceObserver observer) {
    return new DiagnosticRuntime(
        new BoundedMappingDiagnostics(4096), localSink, networkSink, 16,
        "diagnostic-runtime-test", observer);
  }

  private static DiagnosticRuntime runtime(
      DiagnosticFrameSink localSink,
      DiagnosticFrameSink networkSink,
      DiagnosticRuntime.AcceptanceObserver observer,
      DiagnosticRuntime.MapperJoinStrategy joinStrategy) {
    return new DiagnosticRuntime(
        new BoundedMappingDiagnostics(4096), localSink, networkSink, 16,
        "diagnostic-runtime-test", observer, joinStrategy);
  }

  private static DiagnosticFrameSink recordingSink(String name, List<String> order) {
    return new DiagnosticFrameSink() {
      @Override
      public boolean offer(EncodedDiagnosticFrame frame) {
        order.add(name);
        return true;
      }

      @Override
      public void drainAndClose(long timeoutMillis) {
      }
    };
  }

  private static void awaitSize(List<String> values, int expected) throws Exception {
    long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(1);
    while (values.size() < expected && System.nanoTime() < deadline) {
      Thread.sleep(5L);
    }
    assertEquals(expected, values.size());
  }

  private static final class RecordingConsumer implements FrameBatchConsumer {
    final CountDownLatch received = new CountDownLatch(1);
    final CopyOnWriteArrayList<EncodedDiagnosticFrame> frames = new CopyOnWriteArrayList<>();

    @Override
    public void writeBatch(List<EncodedDiagnosticFrame> batch, DiagnosticCounters.Snapshot counters) {
      frames.addAll(batch);
      received.countDown();
    }

    @Override
    public void close() {
    }
  }

  private static final class BlockingAcceptanceObserver
      implements DiagnosticRuntime.AcceptanceObserver {
    final CountDownLatch entered = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);
    final CountDownLatch closeWaitingForAcceptance = new CountDownLatch(1);

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
  }

  private static final class BlockingSinkAcceptanceObserver
      implements AsyncDiagnosticSink.AcceptanceObserver {
    final CountDownLatch entered = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);
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
    public void afterAcceptanceClosed() {
      acceptanceClosed.countDown();
    }
  }

  private static final class CloseEntryObserver implements DiagnosticRuntime.AcceptanceObserver {
    final CountDownLatch acceptanceClosed = new CountDownLatch(1);

    @Override
    public void beforeQueueOffer() {
    }

    @Override
    public void afterAcceptanceClosed() {
      acceptanceClosed.countDown();
    }

  }

  private static final class ControlledMapperJoinStrategy
      implements DiagnosticRuntime.MapperJoinStrategy {
    final CountDownLatch mapperFinished;
    final CountDownLatch waitingForMapper = new CountDownLatch(1);

    ControlledMapperJoinStrategy(CountDownLatch mapperFinished) {
      this.mapperFinished = mapperFinished;
    }

    @Override
    public void join(Thread worker) throws InterruptedException {
      if (!worker.isAlive()) {
        throw new IllegalStateException("mapper exited before join");
      }
      waitingForMapper.countDown();
      mapperFinished.await();
      worker.join();
    }
  }

  private static final class FailingSocket extends Socket {
    int connects;

    @Override
    public void connect(SocketAddress endpoint, int timeout) {
      connects++;
    }

    @Override
    public boolean isConnected() {
      return true;
    }

    @Override
    public OutputStream getOutputStream() {
      return new OutputStream() {
        @Override
        public void write(int value) throws IOException {
          throw new IOException("expected send failure");
        }
      };
    }
  }

  private static final class SuccessfulSocket extends Socket {
    int connects;

    @Override
    public void connect(SocketAddress endpoint, int timeout) {
      connects++;
    }

    @Override
    public boolean isConnected() {
      return true;
    }

    @Override
    public OutputStream getOutputStream() {
      return new OutputStream() {
        @Override
        public void write(int value) {
        }
      };
    }
  }

  private static final class BlockingSocket extends Socket {
    final CountDownLatch writeStarted = new CountDownLatch(1);
    final CountDownLatch closed = new CountDownLatch(1);
    int connects;

    @Override
    public void connect(SocketAddress endpoint, int timeout) {
      connects++;
    }

    @Override
    public boolean isConnected() {
      return connects != 0;
    }

    @Override
    public boolean isClosed() {
      return closed.getCount() == 0L;
    }

    @Override
    public OutputStream getOutputStream() {
      return new OutputStream() {
        @Override
        public void write(int value) throws IOException {
          write(new byte[] {(byte) value});
        }

        @Override
        public void write(byte[] values, int offset, int length) throws IOException {
          writeStarted.countDown();
          try {
            closed.await();
          } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
          }
          throw new IOException("socket closed");
        }
      };
    }

    @Override
    public void close() {
      closed.countDown();
    }
  }

  private static final class RecordingSink implements DiagnosticFrameSink {
    final CountDownLatch offered = new CountDownLatch(1);
    final CountDownLatch closed = new CountDownLatch(1);
    final CopyOnWriteArrayList<EncodedDiagnosticFrame> frames =
        new CopyOnWriteArrayList<>();
    boolean quiesceResult = true;
    int quiesceCalls;

    @Override
    public boolean offer(EncodedDiagnosticFrame frame) {
      frames.add(frame);
      offered.countDown();
      return true;
    }

    @Override
    public boolean quiesceAndClose(long timeoutMillis) {
      quiesceCalls++;
      if (quiesceResult) {
        closed.countDown();
      }
      return quiesceResult;
    }

    @Override
    public void drainAndClose(long timeoutMillis) {
      closed.countDown();
    }
  }

  private static final class BlockingQuiesceSink implements DiagnosticFrameSink {
    final CountDownLatch quiesceStarted = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);
    int quiesceCalls;

    @Override
    public boolean offer(EncodedDiagnosticFrame frame) {
      return true;
    }

    @Override
    public boolean quiesceAndClose(long timeoutMillis) {
      quiesceCalls++;
      quiesceStarted.countDown();
      try {
        release.await();
      } catch (InterruptedException error) {
        Thread.currentThread().interrupt();
        return false;
      }
      return true;
    }

    @Override
    public void drainAndClose(long timeoutMillis) {
      release.countDown();
    }
  }

  private static final class BlockingOfferSink implements DiagnosticFrameSink {
    final CountDownLatch offerStarted = new CountDownLatch(1);
    final CountDownLatch offerFinished = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);
    final CountDownLatch closed = new CountDownLatch(1);

    @Override
    public boolean offer(EncodedDiagnosticFrame frame) {
      offerStarted.countDown();
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
      offerFinished.countDown();
      return true;
    }

    @Override
    public void drainAndClose(long timeoutMillis) {
      closed.countDown();
    }
  }

  private static final class BlockingConsumer implements FrameBatchConsumer {
    final CountDownLatch release = new CountDownLatch(1);

    @Override
    public void writeBatch(List<EncodedDiagnosticFrame> batch, DiagnosticCounters.Snapshot counters)
        throws IOException {
      try {
        if (!release.await(1, TimeUnit.SECONDS)) {
          throw new IOException("test consumer was not released");
        }
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

}

package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import com.naver.map.core.common.model.SafeControlItem;
import com.naver.map.core.common.model.SafetySign;
import com.naver.map.core.navigation.NaviStatusBroadcaster;
import com.naver.map.core.navigation.model.CurrentRoute;
import com.naver.map.core.navigation.model.NaviRouteData;
import com.naver.map.core.navigation.model.TbtDataItem;
import com.naver.map.core.navigation.model.TbtItem;
import com.naver.maps.geometry.LatLng;
import com.naver.maps.navi.v2.api.guidance.model.GuidanceSafety;
import com.naver.maps.navi.v2.shared.api.route.constants.SafetyCode;
import com.naver.maps.navi.v2.shared.api.route.constants.TurnPointType;
import com.naver.maps.navi.v2.shared.api.route.model.RouteInfoImpl;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

final class ProductionHooksTest {
  @Test
  void hooksInjectAllSixProductionNavigationChannelsIntoProductionRuntime() {
    RecordingRuntime runtime = new RecordingRuntime();
    ProductionHooks.setRuntimeForTesting(runtime);
    try {
      ProductionHooks.onStatus(new NaviStatusBroadcaster.Status.Guiding());
      ProductionHooks.onCurrentTbt(new TbtItem(
          10.0, new TbtDataItem(TurnPointType.Left, "main", "road")));
      ProductionHooks.onNextTbt(new TbtItem(
          20.0, new TbtDataItem(TurnPointType.Right, "next", "next-road")));
      ProductionHooks.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 25.0));
      ProductionHooks.onSafety(null);
      ProductionHooks.onRoute(new CurrentRoute(new NaviRouteData(new RouteInfoImpl(List.of(
          new LatLng(37.5, 127.1), new LatLng(37.6, 127.2))))));
    } finally {
      ProductionHooks.resetRuntimeForTesting();
    }

    assertEquals(List.of("status", "current", "next", "safety_source", "safety", "route"),
        runtime.channels);
    assertTrue(runtime.snapshotJson().contains("\"route\":{\"present\":true"));
    assertEquals("naver-6.8.0.5-public-beta-v2", ProductionHooks.PAYLOAD_BUILD_ID);
  }

  @Test
  void runtimePairsMappedGuidanceSafetyDistanceWithFinalSpeedBumpItemOnce() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(
        new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
            "abababab-1111-1111-1111-111111111111")),
        sender);

    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 37.5));
    runtime.onSafety(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null),
        null, null));

    NaverNavigationState state = sender.offered.get(sender.offered.size() - 1);
    assertTrue(state.safety.present);
    assertEquals("speed_bump", state.safety.kind);
    assertEquals(37.5, state.safety.distanceM, 0.0);

    runtime.onSafety(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null),
        null, null));
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
  }

  @Test
  void invalidSafetySourceDoesNotPoisonTheNextValidPair() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(
        new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
            "abababab-2222-2222-2222-222222222222")),
        sender);
    SafeControlItem finalBump = new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null),
        null, null);

    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedCam, 20.0));
    runtime.onSafety(finalBump);
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);

    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 19.0));
    runtime.onSafety(finalBump);
    NaverNavigationState recovered = sender.offered.get(sender.offered.size() - 1);
    assertTrue(recovered.safety.present);
    assertEquals(19.0, recovered.safety.distanceM, 0.0);
  }

  @Test
  void bumpSourceCannotCrossStopAndNewGuidanceSession() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 35.0));
    runtime.onStatus(new NaviStatusBroadcaster.Status.Stopped());
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
  }

  @Test
  void multipleUnconsumedBumpSourcesFailClosedInsteadOfGuessingThePair() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 35.0));
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 350.0));
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 18.0));
    runtime.onSafety(finalBump());
    assertEquals(18.0, sender.offered.get(sender.offered.size() - 1).safety.distanceM);
  }

  @Test
  void unconsumedBumpSourceExpiresRatherThanReusingOldDistance() throws Exception {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 35.0));
    Thread.sleep(1100L);  // Receiver clock, exceeding the synchronous pairing budget.
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
  }

  private static SafeControlItem finalBump() {
    return new SafeControlItem(new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null), null, null);
  }

  @Test
  void newerSessionSourceCannotMakeAnOlderFinalObjectLookCurrent() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 35.0));
    runtime.onStatus(new NaviStatusBroadcaster.Status.Stopped());
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 350.0));
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
  }

  @Test
  void ambiguityPersistsUntilAllOutstandingFinalObjectsDrain() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 35.0));
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 350.0));
    runtime.onSafety(finalBump());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 700.0));
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 18.0));
    runtime.onSafety(finalBump());
    assertEquals(18.0, sender.offered.get(sender.offered.size() - 1).safety.distanceM);
  }

  @Test
  void delayedUnconsumedSourceCannotBeReplacedBeforeItsFinalObjectArrives() throws Exception {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 35.0));
    Thread.sleep(1100L);
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 350.0));
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
  }

  @Test
  void aStatusTransitionOnAnotherThreadInvalidatesTheOriginalThreadSource() throws Exception {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(new NaverNavigationAggregator(), sender);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 35.0));
    Thread status = new Thread(() -> {
      runtime.onStatus(new NaviStatusBroadcaster.Status.Stopped());
      runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    });
    status.start();
    status.join(2000L);
    assertFalse(status.isAlive());
    runtime.onSafety(finalBump());
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
  }

  @Test
  void nullAndInvalidSafetySourcesAreSingleUseAndRecoverable() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(
        new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
            "abababab-3333-3333-3333-333333333333")),
        sender);
    SafeControlItem finalBump = new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null),
        null, null);
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());

    double[] invalidDistances = {
        0.0, Double.NaN, Double.POSITIVE_INFINITY,
    };
    runtime.onSafetySource(null);
    runtime.onSafety(finalBump);
    assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
    for (double invalidDistance : invalidDistances) {
      runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, invalidDistance));
      runtime.onSafety(finalBump);
      assertFalse(sender.offered.get(sender.offered.size() - 1).safety.present);
    }

    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 18.0));
    runtime.onSafety(finalBump);
    assertTrue(sender.offered.get(sender.offered.size() - 1).safety.present);
  }

  @Test
  void productionRuntimeHooksOnlyOfferAndNeverPerformInlineTransportIo() {
    ThrowingConnector connector = new ThrowingConnector();
    ProductionRuntime runtime = new ProductionRuntime(
        new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")),
        new NaverNavigationSender(connector, () -> 0L));

    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());

    assertEquals(0, connector.connects);
  }

  @Test
  void runtimeOffersTheAtomicStateReturnedByAggregatorApply() {
    RecordingSender sender = new RecordingSender();
    ProductionRuntime runtime = new ProductionRuntime(
        new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
            "cccccccc-1111-1111-1111-111111111111",
            "dddddddd-1111-1111-1111-111111111111")),
        sender);

    runtime.enqueue("status", new NaviStatusBroadcaster.Status.Guiding());
    runtime.enqueue("status", new NaviStatusBroadcaster.Status.Stopped());
    runtime.enqueue("status", new NaviStatusBroadcaster.Status.Guiding());

    assertEquals("guiding", sender.offered.get(0).lifecycle);
    assertEquals("cccccccc-1111-1111-1111-111111111111", sender.offered.get(0).sessionId);
    assertEquals("stopped", sender.offered.get(1).lifecycle);
    assertEquals("cccccccc-1111-1111-1111-111111111111", sender.offered.get(1).sessionId);
    assertEquals("guiding", sender.offered.get(2).lifecycle);
    assertEquals("dddddddd-1111-1111-1111-111111111111", sender.offered.get(2).sessionId);
  }

  @Test
  void terminalOfferCannotBeOvertakenByNewSessionGuidingOffer() throws Exception {
    ManualClock clock = new ManualClock(0L);
    RecordingConnector connector = new RecordingConnector();
    BlockingTerminalSender sender = new BlockingTerminalSender(connector, clock);
    ProductionRuntime runtime = new ProductionRuntime(
        new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
            "eeeeeeee-1111-1111-1111-111111111111",
            "ffffffff-1111-1111-1111-111111111111")),
        sender);

    runtime.enqueue("status", new NaviStatusBroadcaster.Status.Guiding());
    sender.flushOnceForTesting();
    clock.nowMs = 500L;
    sender.flushOnceForTesting();
    assertEquals(2, connector.output.frames().size());
    assertEquals(1L, sequence(connector.output.frames().get(0)));
    assertEquals(2L, sequence(connector.output.frames().get(1)));
    assertTrue(connector.output.frames().get(0)
        .contains("eeeeeeee-1111-1111-1111-111111111111"));

    Thread stopper = new Thread(() ->
        runtime.enqueue("status", new NaviStatusBroadcaster.Status.Stopped()));
    stopper.start();
    assertTrue(sender.terminalOfferStarted.await(1, TimeUnit.SECONDS));

    CountDownLatch restartFinished = new CountDownLatch(1);
    Thread restart = new Thread(() -> {
      runtime.enqueue("status", new NaviStatusBroadcaster.Status.Guiding());
      restartFinished.countDown();
    });
    restart.start();

    assertEquals(2, sender.offered.size());
    assertEquals("stopped", sender.offered.get(1).lifecycle);
    assertFalse(restartFinished.await(100, TimeUnit.MILLISECONDS));

    sender.releaseTerminal.countDown();
    stopper.join(1000L);
    restart.join(1000L);

    assertEquals(3, sender.offered.size());
    assertEquals("stopped", sender.offered.get(1).lifecycle);
    assertEquals("guiding", sender.offered.get(2).lifecycle);
    assertEquals("ffffffff-1111-1111-1111-111111111111", sender.offered.get(2).sessionId);

    sender.flushOnceForTesting();
    sender.flushOnceForTesting();
    clock.nowMs = 1000L;
    sender.flushOnceForTesting();

    assertEquals(5, connector.output.frames().size());
    String terminalFrame = connector.output.frames().get(2);
    String firstBFrame = connector.output.frames().get(3);
    String heartbeatBFrame = connector.output.frames().get(4);
    assertTrue(terminalFrame.contains("eeeeeeee-1111-1111-1111-111111111111"));
    assertTrue(terminalFrame.contains("\"lifecycle\":\"stopped\""));
    assertTrue(firstBFrame.contains("ffffffff-1111-1111-1111-111111111111"));
    assertTrue(firstBFrame.contains("\"lifecycle\":\"guiding\""));
    assertTrue(sequence(terminalFrame) > sequence(connector.output.frames().get(1)));
    assertTrue(sequence(heartbeatBFrame) > sequence(firstBFrame));
  }

  private static final class RecordingRuntime extends ProductionRuntime {
    final List<String> channels = new CopyOnWriteArrayList<>();

    RecordingRuntime() {
      super(new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
          "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")), NaverNavigationSender.noopForTesting());
    }

    @Override
    public void onStatus(Object status) {
      channels.add("status");
      super.onStatus(status);
    }

    @Override
    public void onCurrentTbt(Object item) {
      channels.add("current");
      super.onCurrentTbt(item);
    }

    @Override
    public void onNextTbt(Object item) {
      channels.add("next");
      super.onNextTbt(item);
    }

    @Override
    public void onSafetySource(Object item) {
      channels.add("safety_source");
      super.onSafetySource(item);
    }

    @Override
    public void onSafety(Object item) {
      channels.add("safety");
      super.onSafety(item);
    }

    @Override
    public void onRoute(Object item) {
      channels.add("route");
      super.onRoute(item);
    }
  }

  private static GuidanceSafety guidanceSafety(SafetyCode code, double distanceM) {
    return new GuidanceSafety() {
      @Override
      public SafetyCode getCode() {
        return code;
      }

      @Override
      public double distance() {
        return distanceM;
      }
    };
  }

  private static final class ThrowingConnector implements NaverNavigationSender.Connector {
    int connects;

    @Override
    public java.io.OutputStream connect() {
      connects++;
      throw new AssertionError("hook path must not connect inline");
    }
  }

  private static final class RecordingSender extends NaverNavigationSender {
    final List<NaverNavigationState> offered = new CopyOnWriteArrayList<>();

    RecordingSender() {
      super(() -> new java.io.ByteArrayOutputStream(), () -> 0L);
    }

    @Override
    public synchronized void offer(NaverNavigationState state) {
      offered.add(state);
      super.offer(state);
    }
  }

  private static final class BlockingTerminalSender extends NaverNavigationSender {
    final List<NaverNavigationState> offered = new CopyOnWriteArrayList<>();
    final CountDownLatch terminalOfferStarted = new CountDownLatch(1);
    final CountDownLatch releaseTerminal = new CountDownLatch(1);

    BlockingTerminalSender() {
      this(new RecordingConnector(), new ManualClock(0L));
    }

    BlockingTerminalSender(Connector connector, Clock clock) {
      super(connector, clock);
    }

    @Override
    public void offer(NaverNavigationState state) {
      offered.add(state);
      if (state != null && state.isTerminal()) {
        terminalOfferStarted.countDown();
        try {
          releaseTerminal.await(1, TimeUnit.SECONDS);
        } catch (InterruptedException error) {
          Thread.currentThread().interrupt();
        }
      }
      super.offer(state);
    }
  }

  private static long sequence(String frame) {
    String marker = "\"sequence\":";
    int start = frame.indexOf(marker);
    if (start < 0) {
      return -1L;
    }
    start += marker.length();
    int end = start;
    while (end < frame.length() && Character.isDigit(frame.charAt(end))) {
      end++;
    }
    return Long.parseLong(frame.substring(start, end));
  }

  private static final class ManualClock implements NaverNavigationSender.Clock {
    long nowMs;

    ManualClock(long nowMs) {
      this.nowMs = nowMs;
    }

    @Override
    public long nowMs() {
      return nowMs;
    }
  }

  private static final class RecordingConnector implements NaverNavigationSender.Connector {
    final RecordingOutput output = new RecordingOutput();

    @Override
    public OutputStream connect() {
      return output;
    }
  }

  private static final class RecordingOutput extends ByteArrayOutputStream {
    List<String> frames() {
      String text = toString();
      if (text.isEmpty()) {
        return List.of();
      }
      return List.of(text.split("\n"));
    }
  }
}

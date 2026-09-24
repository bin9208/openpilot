package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.nio.ByteBuffer;
import java.nio.channels.WritableByteChannel;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

final class NaverNavigationSenderTest {
  @Test
  void discoveryRejectionLogsOnlySanitizedReasonCodes() throws Exception {
    InetAddress loopback = InetAddress.getLoopbackAddress();
    NaverNavigationSender.DiscoveryTcpConnector connector =
        NaverNavigationSender.DiscoveryTcpConnector.forTesting(
            "127.0.0.1", 7706, 7705, 7712, 100);
    String valid = discoveryResponse("127.0.0.1", 7712);
    android.util.Log.reset();
    assertNull(connector.parseResponse(valid, loopback, 7707));
    assertNull(connector.parseResponse(valid + " trailing", loopback, 7706));
    assertNull(connector.parseResponse(
        valid.replace("discover.response", "discover.wrong"), loopback, 7706));
    assertNull(connector.parseResponse(valid.replace(
        "\"server\":\"127.0.0.1\"", "\"server\":\"127.0.0.2\""),
        loopback,
        7706));
    assertNull(connector.parseResponse(
        valid.replace("\"lease_ms\":2000", "\"lease_ms\":0"), loopback, 7706));

    List<String> entries = android.util.Log.entries();
    assertEquals(
        List.of(
            "NAVER_TRANSPORT response_unsafe_endpoint",
            "NAVER_TRANSPORT response_invalid_shape",
            "NAVER_TRANSPORT response_type_mismatch",
            "NAVER_TRANSPORT response_server_mismatch",
            "NAVER_TRANSPORT response_contract_mismatch"), entries);
    String trace = String.join("\n", entries);
    assertFalse(trace.contains("127.0.0.1"));
    assertFalse(trace.contains("127.0.0.2"));
    assertFalse(trace.contains("discover.response"));
  }

  @Test
  void productionDiscoveryDeterministicallyPrefersWifiOverPrivateCellular() throws Exception {
    NaverNavigationSender.DiscoveryBindCandidate cellular =
        new NaverNavigationSender.DiscoveryBindCandidate(
            "rmnet_data0",
            InetAddress.getByName("10.0.0.2"),
            InetAddress.getByName("10.0.0.255"));
    NaverNavigationSender.DiscoveryBindCandidate wifi =
        new NaverNavigationSender.DiscoveryBindCandidate(
            "wlan0",
            InetAddress.getByName("192.168.50.11"),
            InetAddress.getByName("192.168.50.255"));

    assertEquals(wifi,
        NaverNavigationSender.DiscoveryTcpConnector.preferredBindCandidate(
            List.of(cellular, wifi)));
    assertEquals(wifi,
        NaverNavigationSender.DiscoveryTcpConnector.preferredBindCandidate(
            List.of(wifi, cellular)));
    assertNull(NaverNavigationSender.DiscoveryTcpConnector.preferredBindCandidate(
        List.of(new NaverNavigationSender.DiscoveryBindCandidate(
            "rmnet_data1",
            InetAddress.getByName("192.0.0.2"),
            InetAddress.getByName("192.0.0.31")))));
  }

  @Test
  void boundedChannelWriteTimeoutClosesTheStalledTransport() {
    NeverWritableChannel channel = new NeverWritableChannel();
    NeverReadyWaiter waiter = new NeverReadyWaiter();
    OutputStream output = new NaverNavigationSender.BoundedChannelOutputStream(
        channel, waiter, 10);

    assertThrows(SocketTimeoutException.class,
        () -> output.write("frame\n".getBytes(StandardCharsets.UTF_8)));
    assertFalse(channel.isOpen());
    assertFalse(waiter.isOpen());
  }

  @Test
  void timedOutTerminalWriteIsRetainedAndRecoveredAfterReconnect() {
    TimedOutThenRecordingConnector connector = new TimedOutThenRecordingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState terminal = NaverNavigationState.terminal(
        "67676767-0000-0000-0000-000000000000", 2L, 1L, "stopped");

    sender.offer(terminal);
    sender.flushOnceForTesting();
    assertEquals(1, sender.pendingSlotCountForTesting());
    clock.nowMs = 1000L;
    sender.flushOnceForTesting();

    assertEquals(2, connector.connects);
    assertEquals(0, sender.pendingSlotCountForTesting());
    assertTrue(connector.second.text().contains("\"lifecycle\":\"stopped\""));
  }

  @Test
  void discoveryUsesBoundClientPortAndConnectsToValidatedPacketSource() throws Exception {
    InetAddress loopback = InetAddress.getLoopbackAddress();
    try (DatagramSocket discovery = new DatagramSocket(0, loopback);
         DatagramSocket responsePortProbe = new DatagramSocket(0, loopback);
         ServerSocket tcp = new ServerSocket(0, 16, loopback)) {
      int responsePort = responsePortProbe.getLocalPort();
      responsePortProbe.close();
      CountDownLatch requestReceived = new CountDownLatch(1);
      CountDownLatch tcpConnected = new CountDownLatch(1);
      List<String> requests = new CopyOnWriteArrayList<>();
      List<Integer> requestSourcePorts = new CopyOnWriteArrayList<>();
      Thread responder = new Thread(() -> {
        try {
          DatagramPacket request = new DatagramPacket(new byte[512], 512);
          discovery.receive(request);
          requests.add(new String(
              request.getData(), request.getOffset(), request.getLength(), StandardCharsets.UTF_8));
          requestSourcePorts.add(request.getPort());
          requestReceived.countDown();
          String response = discoveryResponse("127.0.0.1", tcp.getLocalPort());
          byte[] bytes = response.getBytes(StandardCharsets.UTF_8);
          discovery.send(new DatagramPacket(bytes, bytes.length, request.getAddress(), request.getPort()));
        } catch (IOException ignored) {
        }
      }, "naver-discovery-test-responder");
      Thread receiver = new Thread(() -> {
        try (Socket ignored = tcp.accept()) {
          tcpConnected.countDown();
        } catch (IOException ignored) {
        }
      }, "naver-discovery-test-tcp");
      responder.setDaemon(true);
      receiver.setDaemon(true);
      responder.start();
      receiver.start();

      NaverNavigationSender.DiscoveryTcpConnector connector =
          NaverNavigationSender.DiscoveryTcpConnector.forTesting(
              "127.0.0.1", discovery.getLocalPort(), responsePort, tcp.getLocalPort(), 500);
      try (OutputStream output = connector.connect()) {
        assertTrue(requestReceived.await(1, TimeUnit.SECONDS));
        assertTrue(tcpConnected.await(1, TimeUnit.SECONDS));
      }

      assertEquals(List.of(
          "{\"type\":\"carrot.navigation.discover\",\"source\":\"naver\",\"schema_version\":1}"),
          requests);
      assertEquals(List.of(responsePort), requestSourcePorts);
      responder.join(1000L);
      receiver.join(1000L);
    }
  }

  @Test
  void discoveryResponseRequiresExactTypedC3ContractAndSafePacketEndpoint() throws Exception {
    InetAddress loopback = InetAddress.getLoopbackAddress();
    NaverNavigationSender.DiscoveryTcpConnector connector =
        NaverNavigationSender.DiscoveryTcpConnector.forTesting(
            "127.0.0.1", 7706, 7705, 7712, 100);
    String valid = discoveryResponse("127.0.0.1", 7712);

    assertEquals(new InetSocketAddress(loopback, 7712),
        connector.parseResponse(valid, loopback, 7706));
    assertNull(connector.parseResponse(valid, loopback, 7707));
    assertNull(connector.parseResponse(valid.replace(
        "discover.response", "discover.wrong"), loopback, 7706));
    assertNull(connector.parseResponse(valid.replace(
        "\"server\":\"127.0.0.1\"", "\"server\":\"127.0.0.2\""), loopback, 7706));
    assertNull(connector.parseResponse(valid.replace("\"port\":7712", "\"port\":7713"),
        loopback, 7706));
    assertNull(connector.parseResponse(valid.replace("\"port\":7712", "\"port\":7712.0"),
        loopback, 7706));
    assertNull(connector.parseResponse(valid.replace("\"schema\":1", "\"schema\":2"),
        loopback, 7706));
    assertNull(connector.parseResponse(valid.replace(
        "\"schema_version\":1", "\"schema_version\":\"1\""), loopback, 7706));
    assertNull(connector.parseResponse(valid.replace("\"lease_ms\":2000", "\"lease_ms\":0"),
        loopback, 7706));
    assertNull(connector.parseResponse(valid.replace(
        "\"port\":7712", "\"port\":7712,\"port\":7712"), loopback, 7706));
    assertNull(connector.parseResponse(valid.substring(0, valid.length() - 1)
        + ",\"extra\":0}" , loopback, 7706));
    assertNull(connector.parseResponse(valid + " trailing", loopback, 7706));
    assertNull(connector.parseResponse(valid, InetAddress.getByName("0.0.0.0"), 7706));
    assertNull(connector.parseResponse(
        discoveryResponse("0.1.2.3", 7712), InetAddress.getByName("0.1.2.3"), 7706));
    assertNull(connector.parseResponse(valid, InetAddress.getByName("224.0.0.1"), 7706));
    assertNull(connector.parseResponse(
        discoveryResponse("240.0.0.1", 7712), InetAddress.getByName("240.0.0.1"), 7706));
    assertNull(connector.parseResponse(valid, InetAddress.getByName("255.255.255.255"), 7706));
    assertNull(NaverNavigationSender.DiscoveryTcpConnector.createDefault()
        .parseResponse(valid, loopback, 7706));
  }

  @Test
  void discoveryResponseRejectsEnumeratedDirectedBroadcastButKeepsUnicast() throws Exception {
    NaverNavigationSender.DiscoveryTcpConnector connector =
        NaverNavigationSender.DiscoveryTcpConnector.forTesting(
            "127.0.0.1", 7706, 7705, 7712, 100);
    InetAddress directedBroadcast = InetAddress.getByName("192.168.43.255");
    InetAddress networkAddress = NaverNavigationSender.DiscoveryTcpConnector.subnetNetworkAddress(
        InetAddress.getByName("192.168.43.7"), (short) 24);
    InetAddress unicast = InetAddress.getByName("192.168.43.2");
    Set<InetAddress> forbiddenResponders = Set.of(directedBroadcast, networkAddress);

    assertNull(connector.parseResponse(
        discoveryResponse("192.168.43.255", 7712),
        directedBroadcast,
        7706,
        forbiddenResponders));
    assertNull(connector.parseResponse(
        discoveryResponse("192.168.43.0", 7712),
        networkAddress,
        7706,
        forbiddenResponders));
    assertEquals(
        new InetSocketAddress(unicast, 7712),
        connector.parseResponse(
            discoveryResponse("192.168.43.2", 7712), unicast, 7706, forbiddenResponders));
  }

  private static String discoveryResponse(String server, int port) {
    return "{\"type\":\"carrot.navigation.discover.response\",\"server\":\"" + server
        + "\",\"port\":" + port
        + ",\"schema\":1,\"schema_version\":1,\"lease_ms\":2000}";
  }

  @Test
  void transportLossDoesNotClearAggregatorSnapshotAndTerminalIsRetainedForReconnect() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "77777777-7777-7777-7777-777777777777"));
    aggregator.onStatus(new com.naver.map.core.navigation.NaviStatusBroadcaster.Status.Guiding(), 1L);
    NaverNavigationState beforeLoss = aggregator.snapshot(2L);
    FlakyConnector connector = new FlakyConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);

    sender.offer(beforeLoss);
    sender.flushOnceForTesting();
    NaverNavigationState afterLoss = aggregator.snapshot(3L);
    aggregator.onStatus(new com.naver.map.core.navigation.NaviStatusBroadcaster.Status.Stopped(), 4L);
    NaverNavigationState terminal = aggregator.snapshot(5L);
    sender.offer(terminal);
    clock.nowMs = 1000L;
    sender.flushOnceForTesting();

    assertEquals(beforeLoss.sessionId, afterLoss.sessionId);
    assertEquals("guiding", afterLoss.lifecycle);
    assertEquals(1, connector.failures);
    assertTrue(connector.second.text().contains("\"lifecycle\":\"stopped\""));
  }

  @Test
  void heartbeatSendsLatestFullStateOnlyAfterFiveHundredMilliseconds() {
    RecordingConnector connector = new RecordingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState first = NaverNavigationState.guiding(
        "88888888-8888-8888-8888-888888888888", 1L, 0L,
        new Naver6805ObjectMapper.Guidance(true, "left", 10.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());
    NaverNavigationState second = NaverNavigationState.guiding(
        "88888888-8888-8888-8888-888888888888", 2L, 100L,
        new Naver6805ObjectMapper.Guidance(true, "right", 20.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());

    sender.offer(first);
    sender.offer(second);
    assertEquals(1, sender.pendingSlotCountForTesting());
    sender.flushOnceForTesting();
    sender.flushOnceForTesting();
    clock.nowMs = 499L;
    sender.flushOnceForTesting();
    clock.nowMs = 500L;
    sender.flushOnceForTesting();

    assertEquals(1, connector.connects);
    assertEquals(2, connector.output.frames().size());
    assertFalse(connector.output.frames().get(0).contains("\"sequence\":1"));
    assertTrue(connector.output.frames().get(0).contains("\"sequence\":2"));
    assertTrue(connector.output.frames().get(1).contains("\"sequence\":3"));
    assertTrue(connector.output.frames().get(0).contains("\"sentMonotonicMs\":0"));
    assertTrue(connector.output.frames().get(1).contains("\"sentMonotonicMs\":500"));
  }

  @Test
  void heartbeatRetainsEmbeddedSafetyObservationRevision() {
    RecordingConnector connector = new RecordingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "89898989-1111-1111-1111-111111111111"));
    aggregator.onStatus(new com.naver.map.core.navigation.NaviStatusBroadcaster.Status.Guiding(), 1L);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.safety(
        new Naver6805ObjectMapper.Safety(true, "fixed_camera", 400.0, 60)), 2L);

    sender.offer(aggregator.snapshot(3L));
    sender.flushOnceForTesting();
    clock.nowMs = 500L;
    sender.flushOnceForTesting();

    assertEquals(2, connector.output.frames().size());
    assertTrue(connector.output.frames().get(0).contains("\"revision\":1"));
    assertTrue(connector.output.frames().get(1).contains("\"revision\":1"));
    assertTrue(connector.output.frames().get(0).contains("\"sequence\":2"));
    assertTrue(connector.output.frames().get(1).contains("\"sequence\":3"));
  }

  @Test
  void oversizedFrameCapUsesSerializedUtf8FrameBytes() {
    String oversizedSerializedFrame = "{\"payload\":\"" + "x".repeat(300000) + "\"}";

    assertTrue(NaverNavigationSender.frameTooLarge(
        (oversizedSerializedFrame + "\n").getBytes(StandardCharsets.UTF_8)));
  }

  @Test
  void maximalValidEnvelopeStaysBelowTransportCapAndStillSends() {
    RecordingConnector connector = new RecordingConnector();
    NaverNavigationSender sender = new NaverNavigationSender(connector, new ManualClock(0L));
    String maxText = "한".repeat(256);
    NaverNavigationState valid = NaverNavigationState.guiding(
        "99999999-9999-9999-9999-999999999999", 1L, 0L,
        new Naver6805ObjectMapper.Guidance(true, "left", 10.0, maxText, maxText),
        new Naver6805ObjectMapper.Guidance(true, "right", 20.0, maxText, maxText),
        new Naver6805ObjectMapper.Safety(true, "section_camera", 30.0, 250));

    assertTrue(valid.isFrameEligible());
    String json = NaverNavigationEnvelope.toJson(valid.forTransmission(1L, 0L));
    assertFalse(NaverNavigationSender.frameTooLarge(
        (json + "\n").getBytes(StandardCharsets.UTF_8)));
    sender.offer(valid);
    sender.flushOnceForTesting();

    assertEquals(1, connector.output.frames().size());
  }

  @Test
  void maximal4096PointRouteEnvelopeStaysWithinTransportCap() {
    List<Naver6805ObjectMapper.RoutePoint> points = new ArrayList<>();
    for (int index = 0; index < 4096; index++) {
      points.add(index % 2 == 0
          ? new Naver6805ObjectMapper.RoutePoint(-90.0, -180.0)
          : new Naver6805ObjectMapper.RoutePoint(90.0, 180.0));
    }
    Naver6805ObjectMapper.Route route = new Naver6805ObjectMapper.Route(
        true, points, points.size(), points.size(), "route_ok");
    NaverNavigationState state = NaverNavigationState.guiding(
        "91919191-9999-9999-9999-999999999999",
        1L,
        0L,
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent(),
        0L,
        route,
        1L);

    byte[] frame = (NaverNavigationEnvelope.toJson(state) + "\n")
        .getBytes(StandardCharsets.UTF_8);

    assertTrue(state.isFrameEligible());
    assertTrue(frame.length <= 262144);
    assertFalse(NaverNavigationSender.frameTooLarge(frame));
  }

  @Test
  void heartbeatRetainsEmbeddedRouteContentRevision() {
    RecordingConnector connector = new RecordingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    Naver6805ObjectMapper.Route route = new Naver6805ObjectMapper.Route(
        true,
        List.of(
            new Naver6805ObjectMapper.RoutePoint(37.5, 127.1),
            new Naver6805ObjectMapper.RoutePoint(37.6, 127.2)),
        2,
        2,
        "route_ok");
    NaverNavigationState state = NaverNavigationState.guiding(
        "92929292-9999-9999-9999-999999999999",
        1L,
        0L,
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent(),
        0L,
        route,
        4L);

    sender.offer(state);
    sender.flushOnceForTesting();
    clock.nowMs = 500L;
    sender.flushOnceForTesting();

    assertEquals(2, connector.output.frames().size());
    assertTrue(connector.output.frames().get(0).contains("\"revision\":4"));
    assertTrue(connector.output.frames().get(1).contains("\"revision\":4"));
  }

  @Test
  void terminalUsesSecondSlotAndSendsImmediatelyBeforeHeartbeatDeadline() {
    RecordingConnector connector = new RecordingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState active = NaverNavigationState.guiding(
        "aaaaaaaa-0000-0000-0000-000000000000", 1L, 0L,
        new Naver6805ObjectMapper.Guidance(true, "left", 10.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());
    NaverNavigationState terminal = NaverNavigationState.terminal(
        "aaaaaaaa-0000-0000-0000-000000000000", 2L, 1L, "arrived");

    sender.offer(active);
    sender.flushOnceForTesting();
    clock.nowMs = 1L;
    sender.offer(terminal);
    assertEquals(2, sender.pendingSlotCountForTesting());
    sender.flushOnceForTesting();

    assertEquals(2, connector.output.frames().size());
    assertTrue(connector.output.frames().get(1).contains("\"lifecycle\":\"arrived\""));
  }

  @Test
  void terminalSuccessKeepsNewerDifferentSessionLatestSlot() {
    RecordingConnector connector = new RecordingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState oldTerminal = NaverNavigationState.terminal(
        "cccccccc-0000-0000-0000-000000000000", 2L, 1L, "stopped");
    NaverNavigationState newLatest = NaverNavigationState.guiding(
        "dddddddd-0000-0000-0000-000000000000", 1L, 2L,
        new Naver6805ObjectMapper.Guidance(true, "left", 10.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());

    sender.offer(newLatest);
    sender.offer(oldTerminal);
    sender.flushOnceForTesting();
    clock.nowMs = 500L;
    sender.flushOnceForTesting();

    assertEquals(2, connector.output.frames().size());
    assertTrue(connector.output.frames().get(0).contains("cccccccc-0000-0000-0000-000000000000"));
    assertTrue(connector.output.frames().get(1).contains("dddddddd-0000-0000-0000-000000000000"));
  }

  @Test
  void failedReconnectUsesBoundedBackoffAndOfferDoesNotBlockOnTransport() {
    AlwaysFailConnector connector = new AlwaysFailConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState active = NaverNavigationState.guiding(
        "bbbbbbbb-0000-0000-0000-000000000000", 1L, 0L,
        new Naver6805ObjectMapper.Guidance(true, "left", 10.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());

    sender.offer(active);
    assertEquals(0, connector.connects);
    sender.flushOnceForTesting();
    assertEquals(1, connector.connects);
    assertTrue(sender.reconnectBackoffMsForTesting() >= 1000L);
    assertTrue(sender.reconnectBackoffMsForTesting() <= 30000L);
    sender.flushOnceForTesting();
    assertEquals(1, connector.connects);
    clock.nowMs = sender.reconnectBackoffMsForTesting();
    sender.flushOnceForTesting();
    assertEquals(2, connector.connects);
  }

  @Test
  void offerDoesNotWaitForBlockedTransportWrite() throws Exception {
    BlockingWriteConnector connector = new BlockingWriteConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState active = NaverNavigationState.guiding(
        "eeeeeeee-0000-0000-0000-000000000000", 1L, 0L,
        new Naver6805ObjectMapper.Guidance(true, "left", 10.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());
    NaverNavigationState terminal = NaverNavigationState.terminal(
        "eeeeeeee-0000-0000-0000-000000000000", 2L, 1L, "stopped");
    Thread flusher = new Thread(() -> {
      sender.offer(active);
      sender.flushOnceForTesting();
    });
    flusher.start();
    assertTrue(connector.output.writeStarted.await(1, TimeUnit.SECONDS));

    CountDownLatch offered = new CountDownLatch(1);
    Thread offerThread = new Thread(() -> {
      sender.offer(terminal);
      offered.countDown();
    });
    offerThread.start();

    assertTrue(offered.await(100, TimeUnit.MILLISECONDS));
    connector.output.release.countDown();
    flusher.join(1000L);
    offerThread.join(1000L);
  }

  @Test
  void startedWorkerDoesNotHoldSenderMonitorDuringTransportWrite() throws Exception {
    BlockingWriteConnector connector = new BlockingWriteConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState active = NaverNavigationState.guiding(
        "abababab-0000-0000-0000-000000000000", 1L, 0L,
        new Naver6805ObjectMapper.Guidance(true, "left", 10.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());
    NaverNavigationState terminal = NaverNavigationState.terminal(
        "abababab-0000-0000-0000-000000000000", 2L, 1L, "stopped");

    sender.start();
    sender.offer(active);
    assertTrue(connector.output.writeStarted.await(1, TimeUnit.SECONDS));

    CountDownLatch offered = new CountDownLatch(1);
    Thread offerThread = new Thread(() -> {
      sender.offer(terminal);
      offered.countDown();
    });
    offerThread.start();

    assertTrue(offered.await(100, TimeUnit.MILLISECONDS));
    connector.output.release.countDown();
    offerThread.join(1000L);
  }

  @Test
  void terminalReconnectHonorsBoundedBackoffAfterFailure() {
    AlwaysFailConnector connector = new AlwaysFailConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    NaverNavigationState terminal = NaverNavigationState.terminal(
        "ffffffff-0000-0000-0000-000000000000", 2L, 1L, "stopped");

    sender.offer(terminal);
    sender.flushOnceForTesting();
    clock.nowMs = 500L;
    sender.flushOnceForTesting();
    clock.nowMs = 1000L;
    sender.flushOnceForTesting();

    assertEquals(2, connector.connects);
  }

  @Test
  void startedWorkerHeartbeatIsNotStarvedByRepeatedEarlyLatestOffers() throws Exception {
    LatchingConnector connector = new LatchingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);
    sender.start();
    sender.offer(guiding("12121212-0000-0000-0000-000000000000", 1L, 0L, "left"));
    assertTrue(connector.output.awaitFrameCount(1, 1000L));

    for (int i = 1; i <= 4; i++) {
      clock.nowMs = i * 100L;
      sender.offer(guiding("12121212-0000-0000-0000-000000000000", i + 1L, clock.nowMs, "right"));
      assertTrue(connector.output.awaitFrameCount(i + 1, 250L));
    }
    clock.nowMs = 900L;

    assertTrue(connector.output.awaitFrameCount(6, 650L));
    assertTrue(connector.output.frames().get(5).contains("\"sequence\":6"));
    assertTrue(connector.output.frames().get(5).contains("\"sentMonotonicMs\":900"));
  }

  @Test
  void newSessionLatestDoesNotInheritPreviousSessionHeartbeatGate() {
    RecordingConnector connector = new RecordingConnector();
    ManualClock clock = new ManualClock(0L);
    NaverNavigationSender sender = new NaverNavigationSender(connector, clock);

    sender.offer(guiding("34343434-0000-0000-0000-000000000000", 1L, 0L, "left"));
    sender.flushOnceForTesting();
    clock.nowMs = 100L;
    sender.offer(guiding("56565656-0000-0000-0000-000000000000", 1L, 100L, "right"));
    sender.flushOnceForTesting();

    assertEquals(2, connector.output.frames().size());
    assertTrue(connector.output.frames().get(1).contains("56565656-0000-0000-0000-000000000000"));
    assertTrue(connector.output.frames().get(1).contains("\"sequence\":1"));
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

  private static NaverNavigationState guiding(
      String sessionId, long sequence, long sentMonotonicMs, String maneuver) {
    return NaverNavigationState.guiding(
        sessionId,
        sequence,
        sentMonotonicMs,
        new Naver6805ObjectMapper.Guidance(true, maneuver, 10.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());
  }

  private static final class RecordingConnector implements NaverNavigationSender.Connector {
    int connects;
    final RecordingOutput output = new RecordingOutput();

    @Override
    public OutputStream connect() {
      connects++;
      return output;
    }
  }

  private static final class FlakyConnector implements NaverNavigationSender.Connector {
    int failures;
    final RecordingOutput second = new RecordingOutput();

    @Override
    public OutputStream connect() throws IOException {
      if (failures == 0) {
        failures++;
        throw new IOException("transport down");
      }
      return second;
    }
  }

  private static final class RecordingOutput extends ByteArrayOutputStream {
    String text() {
      return toString();
    }

    List<String> frames() {
      String text = text();
      if (text.isEmpty()) {
        return List.of();
      }
      String[] parts = text.split("\\n");
      return List.of(parts);
    }
  }

  private static final class NeverWritableChannel implements WritableByteChannel {
    private boolean open = true;

    @Override
    public int write(ByteBuffer source) {
      return 0;
    }

    @Override
    public boolean isOpen() {
      return open;
    }

    @Override
    public void close() {
      open = false;
    }
  }

  private static final class NeverReadyWaiter implements NaverNavigationSender.WriteWaiter {
    private boolean open = true;

    @Override
    public boolean awaitWritable(long timeoutMs) {
      return false;
    }

    @Override
    public boolean isOpen() {
      return open;
    }

    @Override
    public void close() {
      open = false;
    }
  }

  private static final class TimedOutThenRecordingConnector
      implements NaverNavigationSender.Connector {
    int connects;
    final RecordingOutput second = new RecordingOutput();

    @Override
    public OutputStream connect() {
      connects++;
      if (connects == 1) {
        return new NaverNavigationSender.BoundedChannelOutputStream(
            new NeverWritableChannel(), new NeverReadyWaiter(), 10);
      }
      return second;
    }
  }

  private static final class LatchingConnector implements NaverNavigationSender.Connector {
    final LatchingOutput output = new LatchingOutput();

    @Override
    public OutputStream connect() {
      return output;
    }
  }

  private static final class LatchingOutput extends ByteArrayOutputStream {
    private int frameCount;

    @Override
    public synchronized void write(byte[] bytes) throws IOException {
      super.write(bytes);
      String text = toString();
      int count = text.isEmpty() ? 0 : text.split("\n").length;
      if (count > frameCount) {
        frameCount = count;
        notifyAll();
      }
    }

    synchronized boolean awaitFrameCount(int expected, long timeoutMs) throws InterruptedException {
      long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMs);
      while (frameCount < expected) {
        long remainingNs = deadline - System.nanoTime();
        if (remainingNs <= 0L) {
          return false;
        }
        wait(Math.min(TimeUnit.NANOSECONDS.toMillis(remainingNs) + 1L, timeoutMs));
      }
      return true;
    }

    synchronized List<String> frames() {
      String text = toString();
      if (text.isEmpty()) {
        return List.of();
      }
      return List.of(text.split("\n"));
    }
  }

  private static final class AlwaysFailConnector implements NaverNavigationSender.Connector {
    int connects;

    @Override
    public OutputStream connect() throws IOException {
      connects++;
      throw new IOException("down");
    }
  }

  private static final class BlockingWriteConnector implements NaverNavigationSender.Connector {
    final BlockingOutput output = new BlockingOutput();

    @Override
    public OutputStream connect() {
      return output;
    }
  }

  private static final class BlockingOutput extends OutputStream {
    final CountDownLatch writeStarted = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);

    @Override
    public void write(int b) {
    }

    @Override
    public void write(byte[] bytes) throws IOException {
      writeStarted.countDown();
      try {
        release.await(1, TimeUnit.SECONDS);
      } catch (InterruptedException error) {
        Thread.currentThread().interrupt();
        throw new IOException(error);
      }
    }
  }
}

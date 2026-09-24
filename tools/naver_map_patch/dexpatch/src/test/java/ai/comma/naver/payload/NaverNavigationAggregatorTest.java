package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import com.naver.map.core.navigation.NaviStatusBroadcaster;
import com.naver.map.core.navigation.model.TbtDataItem;
import com.naver.map.core.navigation.model.TbtItem;
import com.naver.maps.navi.v2.shared.api.route.constants.TurnPointType;
import org.junit.jupiter.api.Test;

final class NaverNavigationAggregatorTest {
  @Test
  void routeRevisionChangesOnlyWhenPresenceOrPointContentChanges() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "10101010-1111-1111-1111-111111111111"));
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.status("guiding"), 1L);
    Naver6805ObjectMapper.Route route = route(37.5, 127.1, 37.6, 127.2);

    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.route(route), 10L);
    assertEquals(1L, aggregator.snapshot(11L).routeRevision);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.current(
        new Naver6805ObjectMapper.Guidance(true, "left", 20.0, "road", "main")), 12L);
    assertEquals(1L, aggregator.snapshot(13L).routeRevision);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.route(route), 14L);
    assertEquals(1L, aggregator.snapshot(15L).routeRevision);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.route(
        route(37.5, 127.1, 37.7, 127.3)), 16L);
    assertEquals(2L, aggregator.snapshot(17L).routeRevision);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.route(
        Naver6805ObjectMapper.Route.absent()), 18L);
    assertEquals(3L, aggregator.snapshot(19L).routeRevision);
    assertFalse(aggregator.snapshot(19L).route.present);
  }

  @Test
  void terminalStateClearsRouteAndNewGuidingSessionStartsWithAbsentRoute() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "20202020-1111-1111-1111-111111111111",
            "30303030-1111-1111-1111-111111111111"));
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.status("guiding"), 1L);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.route(
        route(37.5, 127.1, 37.6, 127.2)), 2L);
    assertTrue(aggregator.snapshot(3L).route.present);
    assertEquals(1L, aggregator.snapshot(3L).routeRevision);

    NaverNavigationState terminal = aggregator.apply(
        Naver6805ObjectMapper.MappedUpdate.status("stopped"), 4L);
    NaverNavigationState restarted = aggregator.apply(
        Naver6805ObjectMapper.MappedUpdate.status("guiding"), 5L);

    assertFalse(terminal.route.present);
    assertEquals(0L, terminal.routeRevision);
    assertFalse(restarted.route.present);
    assertEquals(0L, restarted.routeRevision);
    assertNotEquals(terminal.sessionId, restarted.sessionId);
  }

  @Test
  void terminalSessionsClearControlAndCannotBeReactivatedByLaterGuidance() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222"));

    aggregator.onStatus(new NaviStatusBroadcaster.Status.Guiding(), 10L);
    aggregator.onCurrentGuidance(tbt(120.0, TurnPointType.Left), 20L);
    NaverNavigationState active = aggregator.snapshot(30L);
    aggregator.onStatus(new NaviStatusBroadcaster.Status.Stopped(), 40L);
    NaverNavigationState terminal = aggregator.snapshot(50L);
    aggregator.onCurrentGuidance(tbt(80.0, TurnPointType.Right), 60L);
    NaverNavigationState afterLateGuidance = aggregator.snapshot(70L);
    aggregator.onStatus(new NaviStatusBroadcaster.Status.Guiding(), 80L);
    NaverNavigationState restarted = aggregator.snapshot(90L);

    assertEquals("11111111-1111-1111-1111-111111111111", active.sessionId);
    assertTrue(active.current.present);
    assertEquals("stopped", terminal.lifecycle);
    assertFalse(terminal.current.present);
    assertEquals(terminal.sessionId, afterLateGuidance.sessionId);
    assertEquals("stopped", afterLateGuidance.lifecycle);
    assertFalse(afterLateGuidance.current.present);
    assertEquals("22222222-2222-2222-2222-222222222222", restarted.sessionId);
    assertNotEquals(active.sessionId, restarted.sessionId);
    assertEquals("guiding", restarted.lifecycle);
  }

  @Test
  void arrivedIsAlsoTerminalAndRequiresANewSessionId() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "33333333-3333-3333-3333-333333333333",
            "44444444-4444-4444-4444-444444444444"));

    aggregator.onStatus(new NaviStatusBroadcaster.Status.Guiding(), 1L);
    String first = aggregator.snapshot(2L).sessionId;
    aggregator.onStatus(new NaviStatusBroadcaster.Status.Arrived(), 3L);
    NaverNavigationState arrived = aggregator.snapshot(4L);
    aggregator.onStatus(new NaviStatusBroadcaster.Status.Guiding(), 5L);
    String second = aggregator.snapshot(6L).sessionId;

    assertEquals("33333333-3333-3333-3333-333333333333", first);
    assertEquals("arrived", arrived.lifecycle);
    assertFalse(arrived.current.present);
    assertEquals("44444444-4444-4444-4444-444444444444", second);
  }

  @Test
  void mappedAbsentGuidanceUpdateClearsOnlyThatItem() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "55555555-1111-1111-1111-111111111111"));
    aggregator.onStatus(new NaviStatusBroadcaster.Status.Guiding(), 1L);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.current(
        new Naver6805ObjectMapper.Guidance(true, "left", 20.0, "road", "main")), 2L);
    assertTrue(aggregator.snapshot(3L).current.present);

    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.current(
        Naver6805ObjectMapper.Guidance.absent()), 4L);

    assertFalse(aggregator.snapshot(5L).current.present);
    assertEquals("guiding", aggregator.snapshot(5L).lifecycle);
  }

  @Test
  void applyReturnsAtomicSnapshotBeforeAnyLaterHookCanChangeSession() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "66666666-1111-1111-1111-111111111111",
            "77777777-1111-1111-1111-111111111111"));
    NaverNavigationState firstGuiding = aggregator.apply(
        Naver6805ObjectMapper.MappedUpdate.status("guiding"), 1L);
    NaverNavigationState stopped = aggregator.apply(
        Naver6805ObjectMapper.MappedUpdate.status("stopped"), 2L);
    NaverNavigationState secondGuiding = aggregator.apply(
        Naver6805ObjectMapper.MappedUpdate.status("guiding"), 3L);

    assertEquals("66666666-1111-1111-1111-111111111111", firstGuiding.sessionId);
    assertEquals("guiding", firstGuiding.lifecycle);
    assertEquals("66666666-1111-1111-1111-111111111111", stopped.sessionId);
    assertEquals("stopped", stopped.lifecycle);
    assertFalse(stopped.current.present);
    assertEquals("77777777-1111-1111-1111-111111111111", secondGuiding.sessionId);
    assertEquals("guiding", secondGuiding.lifecycle);
  }

  @Test
  void safetyObservationRevisionChangesOnlyForChangedSafetyAndAbsentSafetyClears() {
    NaverNavigationAggregator aggregator = new NaverNavigationAggregator(
        new NaverNavigationAggregator.FixedSessionIds(
            "88888888-1111-1111-1111-111111111111"));
    Naver6805ObjectMapper.Safety fixed =
        new Naver6805ObjectMapper.Safety(true, "fixed_camera", 400.0, 60);
    aggregator.onStatus(new NaviStatusBroadcaster.Status.Guiding(), 1L);
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.safety(fixed), 2L);
    String first = NaverNavigationEnvelope.toJson(aggregator.snapshot(3L));

    aggregator.onCurrentGuidance(tbt(200.0, TurnPointType.Left), 4L);
    String afterGuidance = NaverNavigationEnvelope.toJson(aggregator.snapshot(5L));
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.safety(fixed), 6L);
    String afterSameSafety = NaverNavigationEnvelope.toJson(aggregator.snapshot(7L));
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.safety(
        new Naver6805ObjectMapper.Safety(true, "fixed_camera", 300.0, 60)), 8L);
    String changed = NaverNavigationEnvelope.toJson(aggregator.snapshot(9L));
    aggregator.apply(Naver6805ObjectMapper.MappedUpdate.safety(
        Naver6805ObjectMapper.Safety.absent()), 10L);
    String absent = NaverNavigationEnvelope.toJson(aggregator.snapshot(11L));

    assertTrue(first.contains("\"revision\":1"));
    assertTrue(afterGuidance.contains("\"revision\":1"));
    assertTrue(afterSameSafety.contains("\"revision\":1"));
    assertTrue(changed.contains("\"revision\":2"));
    assertTrue(absent.contains("\"safety\":{\"present\":false}"));
    assertFalse(absent.contains("\"revision\":"));
  }

  private static TbtItem tbt(double distanceM, TurnPointType type) {
    return new TbtItem(distanceM, new TbtDataItem(type, "main", "road"));
  }

  private static Naver6805ObjectMapper.Route route(double... coordinates) {
    assertEquals(0, coordinates.length % 2);
    java.util.ArrayList<Naver6805ObjectMapper.RoutePoint> points =
        new java.util.ArrayList<Naver6805ObjectMapper.RoutePoint>();
    for (int index = 0; index < coordinates.length; index += 2) {
      points.add(new Naver6805ObjectMapper.RoutePoint(
          coordinates[index], coordinates[index + 1]));
    }
    return new Naver6805ObjectMapper.Route(
        true, points, points.size(), points.size(), "route_ok");
  }
}

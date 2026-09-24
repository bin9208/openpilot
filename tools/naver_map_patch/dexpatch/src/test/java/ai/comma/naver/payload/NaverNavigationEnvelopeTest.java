package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

final class NaverNavigationEnvelopeTest {
  @Test
  void presentRouteUsesStableRevisionNeutralTripFieldsAndLatitudeLongitudePoints() {
    Naver6805ObjectMapper.Route route = new Naver6805ObjectMapper.Route(
        true,
        List.of(
            new Naver6805ObjectMapper.RoutePoint(37.5, 127.1),
            new Naver6805ObjectMapper.RoutePoint(37.6, 127.2)),
        2,
        2,
        "route_ok");
    NaverNavigationState state = NaverNavigationState.guiding(
        "45454545-5555-5555-5555-555555555555",
        7L,
        1234L,
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent(),
        0L,
        route,
        1L);

    String json = NaverNavigationEnvelope.toJson(state);

    assertTrue(json.contains("\"route\":{\"present\":true,\"revision\":1"));
    assertTrue(json.contains("\"remainingDistanceM\":0"));
    assertTrue(json.contains("\"remainingTimeSec\":0"));
    assertTrue(json.contains("\"offRoute\":false"));
    assertTrue(json.contains("\"destinationValid\":false"));
    assertTrue(json.contains("\"points\":[[37.5,127.1],[37.6,127.2]]"));
  }

  @Test
  void invalidPresentRoutesAndNegativeRevisionProduceNoFrame() {
    Naver6805ObjectMapper.Route shortRoute = new Naver6805ObjectMapper.Route(
        true,
        List.of(new Naver6805ObjectMapper.RoutePoint(37.5, 127.1)),
        1,
        1,
        "route_ok");
    Naver6805ObjectMapper.Route invalidCoordinate = new Naver6805ObjectMapper.Route(
        true,
        List.of(
            new Naver6805ObjectMapper.RoutePoint(91.0, 127.1),
            new Naver6805ObjectMapper.RoutePoint(37.6, 127.2)),
        2,
        2,
        "route_ok");
    List<Naver6805ObjectMapper.RoutePoint> tooManyPoints = new ArrayList<>();
    for (int index = 0; index < 4097; index++) {
      tooManyPoints.add(new Naver6805ObjectMapper.RoutePoint(37.5, 127.1));
    }
    Naver6805ObjectMapper.Route oversizedRoute = new Naver6805ObjectMapper.Route(
        true, tooManyPoints, tooManyPoints.size(), tooManyPoints.size(), "route_ok");

    assertEquals("", NaverNavigationEnvelope.toJson(guidingWithRoute(shortRoute, 1L)));
    assertEquals("", NaverNavigationEnvelope.toJson(guidingWithRoute(invalidCoordinate, 1L)));
    assertEquals("", NaverNavigationEnvelope.toJson(guidingWithRoute(oversizedRoute, 1L)));
    assertEquals("", NaverNavigationEnvelope.toJson(guidingWithRoute(validRoute(), -1L)));
  }

  @Test
  void jsonUsesOnlyNaverV1KeysAndAbsentRoadRouteDefaults() {
    NaverNavigationState state = NaverNavigationState.guiding(
        "55555555-5555-5555-5555-555555555555",
        7L,
        1234L,
        new Naver6805ObjectMapper.Guidance(true, "left", 33.0, "road", "main"),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent());

    String json = NaverNavigationEnvelope.toJson(state);

    assertTrue(json.contains("\"schema\":\"naver.navigation.v1\""));
    assertTrue(json.contains("\"sessionId\":\"55555555-5555-5555-5555-555555555555\""));
    assertTrue(json.contains("\"sequence\":7"));
    assertTrue(json.contains("\"sentMonotonicMs\":1234"));
    assertTrue(json.contains("\"guidance\":{\"current\":{\"present\":true"));
    assertTrue(json.contains("\"road\":{\"limitValid\":false,\"categoryValid\":false}"));
    assertTrue(json.contains("\"route\":{\"present\":false}"));
    assertFalse(json.contains("destinationValid"));
    assertFalse(json.contains("points"));
    assertFalse(json.contains("lane"));
  }

  @Test
  void inactiveLifecycleCannotCarryControlData() {
    NaverNavigationState stopped = NaverNavigationState.terminal(
        "66666666-6666-6666-6666-666666666666", 8L, 999L, "stopped");

    String json = NaverNavigationEnvelope.toJson(stopped);

    assertTrue(json.contains("\"lifecycle\":\"stopped\""));
    assertTrue(json.contains("\"current\":{\"present\":false}"));
    assertTrue(json.contains("\"next\":{\"present\":false}"));
    assertTrue(json.contains("\"safety\":{\"present\":false}"));
  }

  @Test
  void invalidLifecycleControlMetadataAndBoundsProduceNoFrame() {
    assertEquals("", NaverNavigationEnvelope.toJson(NaverNavigationState.terminal(
        "not-a-uuid", 1L, 1L, "stopped")));
    assertEquals("", NaverNavigationEnvelope.toJson(NaverNavigationState.guiding(
        "77777777-7777-7777-7777-777777777777",
        -1L,
        1L,
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent())));
    assertEquals("", NaverNavigationEnvelope.toJson(NaverNavigationState.guiding(
        "77777777-7777-7777-7777-777777777777",
        1L,
        -1L,
        new Naver6805ObjectMapper.Guidance(true, "bad", 1.0, "", ""),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent())));
    assertEquals("", NaverNavigationEnvelope.toJson(NaverNavigationState.idle(
        "77777777-7777-7777-7777-777777777777", 1L, 1L)));
  }

  private static NaverNavigationState guidingWithRoute(
      Naver6805ObjectMapper.Route route, long revision) {
    return NaverNavigationState.guiding(
        "56565656-5555-5555-5555-555555555555",
        1L,
        1L,
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Guidance.absent(),
        Naver6805ObjectMapper.Safety.absent(),
        0L,
        route,
        revision);
  }

  private static Naver6805ObjectMapper.Route validRoute() {
    return new Naver6805ObjectMapper.Route(
        true,
        List.of(
            new Naver6805ObjectMapper.RoutePoint(37.5, 127.1),
            new Naver6805ObjectMapper.RoutePoint(37.6, 127.2)),
        2,
        2,
        "route_ok");
  }
}

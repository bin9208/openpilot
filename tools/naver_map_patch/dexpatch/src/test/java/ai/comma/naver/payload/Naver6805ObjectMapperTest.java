package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;
import java.util.stream.IntStream;
import com.naver.map.core.common.model.SafeControlItem;
import com.naver.map.core.common.model.SafetyExtra;
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
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;

final class Naver6805ObjectMapperTest {
  @Test
  void routeMapsConcreteRouteInfoImplementorThroughTheExactInterface() {
    Naver6805ObjectMapper.Route route = Naver6805ObjectMapper.mapRoute(currentRoute(List.of(
        new LatLng(37.5, 127.1), new LatLng(37.6, 127.2))));

    assertTrue(route.present);
    assertEquals(2, route.inputCount);
    assertEquals(2, route.outputCount);
    assertEquals(37.5, route.points.get(0).latitude, 0.0);
    assertEquals(127.1, route.points.get(0).longitude, 0.0);
    assertEquals(37.6, route.points.get(1).latitude, 0.0);
    assertEquals(127.2, route.points.get(1).longitude, 0.0);
    assertThrows(UnsupportedOperationException.class,
        () -> route.points.add(new Naver6805ObjectMapper.RoutePoint(0.0, 0.0)));
  }

  @Test
  void routeRejectsNullShortWrongDescriptorAndInvalidCoordinates() {
    assertFalse(Naver6805ObjectMapper.mapRoute(null).present);
    assertFalse(Naver6805ObjectMapper.mapRoute(currentRoute(List.of())).present);
    assertFalse(Naver6805ObjectMapper.mapRoute(
        currentRoute(List.of(new LatLng(37.5, 127.1)))).present);
    assertFalse(Naver6805ObjectMapper.mapRoute(
        currentRoute(List.of(new Object(), new Object()))).present);
    assertFalse(Naver6805ObjectMapper.mapRoute(currentRoute(List.of(
        new LatLng(Double.NaN, 127.1), new LatLng(37.6, 127.2)))).present);
    assertFalse(Naver6805ObjectMapper.mapRoute(currentRoute(List.of(
        new LatLng(91.0, 127.1), new LatLng(37.6, 127.2)))).present);
    assertFalse(Naver6805ObjectMapper.mapRoute(currentRoute(List.of(
        new LatLng(37.5, Double.POSITIVE_INFINITY), new LatLng(37.6, 127.2)))).present);
    assertFalse(Naver6805ObjectMapper.mapRoute(currentRoute(List.of(
        new LatLng(37.5, 181.0), new LatLng(37.6, 127.2)))).present);
  }

  @Test
  void routeUniformlyLimitsTo4096AndPreservesEndpoints() {
    List<LatLng> input = IntStream.range(0, 5001)
        .mapToObj(i -> new LatLng(35.0 + i * 0.000001, 127.0))
        .collect(Collectors.toList());

    Naver6805ObjectMapper.Route route = Naver6805ObjectMapper.mapRoute(currentRoute(input));

    assertTrue(route.present);
    assertEquals(5001, route.inputCount);
    assertEquals(4096, route.outputCount);
    assertEquals(input.get(0).latitude, route.points.get(0).latitude, 0.0);
    assertEquals(input.get(5000).latitude, route.points.get(4095).latitude, 0.0);
  }

  @Test
  void routeSamplerUsesHalfEvenTieBreaking() {
    assertEquals(2, Naver6805ObjectMapper.sampleIndex(1, 5, 2));
    assertEquals(4, Naver6805ObjectMapper.sampleIndex(1, 7, 2));
  }

  @Test
  void guidanceUsesOnlyAcceptedEnumNamesAndNeverNumericTurnPointValues() {
    Naver6805ObjectMapper.Guidance accepted = Naver6805ObjectMapper.mapGuidance(
        tbt(42.5, TurnPointType.Direction11, "좌측", "강변북로"));
    Naver6805ObjectMapper.Guidance invalid = Naver6805ObjectMapper.mapGuidance(
        tbt(17.0, TurnPointType.AccessUnderpassStraight, "지하차도", "내부순환"));

    assertTrue(accepted.present);
    assertEquals("slight_left", accepted.maneuver);
    assertEquals(42.5, accepted.distanceM, 0.0);
    assertEquals("좌측", accepted.mainText);
    assertEquals("강변북로", accepted.roadName);
    assertFalse(invalid.present);
  }

  @ParameterizedTest
  @CsvSource({
      "Direction11, slight_left",
      "Left, left",
      "Right, right",
      "RightDirection, slight_right",
      "UTurn, u_turn",
      "Goal, arrive"
  })
  void guidanceMapsEveryProductionManeuverName(TurnPointType type, String expected) {
    Naver6805ObjectMapper.Guidance guidance = Naver6805ObjectMapper.mapGuidance(
        tbt(42.5, type, "main", "road"));

    assertTrue(guidance.present);
    assertEquals(expected, guidance.maneuver);
  }

  @ParameterizedTest
  @CsvSource({"AccessUnderpassStraight", "Rest"})
  void guidanceRejectsUnsupportedManeuverNames(TurnPointType type) {
    assertFalse(Naver6805ObjectMapper.mapGuidance(
        tbt(42.5, type, "main", "road")).present);
  }

  @Test
  void guidanceDoesNotRequireUnusedTbtTypeAndUsedAccessorThrowsAreItemLocal() {
    Naver6805ObjectMapper.resetDiagnosticsForTesting();

    Naver6805ObjectMapper.Guidance unusedTypeThrows = Naver6805ObjectMapper.mapGuidance(
        new TbtItem(42.0, new TbtDataItem(TurnPointType.Left, "좌회전", "도로"),
            false, false, true));

    assertTrue(unusedTypeThrows.present);
    assertEquals("left", unusedTypeThrows.maneuver);
    assertGuidanceFailureDoesNotPoisonNextValid(new TbtItem(
        1.0, new TbtDataItem(TurnPointType.Left, "좌회전", "도로"),
        true, false, false));
    assertGuidanceFailureDoesNotPoisonNextValid(new TbtItem(
        1.0, new TbtDataItem(TurnPointType.Left, "좌회전", "도로"),
        false, true, false));
    assertGuidanceFailureDoesNotPoisonNextValid(new TbtItem(
        1.0, new TbtDataItem(TurnPointType.Left, "좌회전", "도로", true, false, false)));
    assertGuidanceFailureDoesNotPoisonNextValid(new TbtItem(
        1.0, new TbtDataItem(TurnPointType.Left, "좌회전", "도로", false, true, false)));
    assertGuidanceFailureDoesNotPoisonNextValid(new TbtItem(
        1.0, new TbtDataItem(TurnPointType.Left, "좌회전", "도로", false, false, true)));

    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("guidance_accessor"));
  }

  @Test
  void safetyMapsExactDistanceCodeAndSpeedContractsFailClosed() {
    Naver6805ObjectMapper.Safety fixed = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(155.5), null));
    Naver6805ObjectMapper.Safety mobile = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.MoveSpeedCam, 70, new SafetyExtra.Distance(80.0), null));
    Naver6805ObjectMapper.Safety section = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.VariableSectionStart, 90, null, new SafetyExtra.Distance(300.0)));
    Naver6805ObjectMapper.Safety bump = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedBump, null, new SafetyExtra.Distance(25.0), null));
    Naver6805ObjectMapper.Safety invalidSpeed = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 0, new SafetyExtra.Distance(55.0), null));
    Naver6805ObjectMapper.Safety ignoredSchoolZone = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 30, new SafetyExtra.SchoolZone(11.0), null));
    Naver6805ObjectMapper.Safety ignoredCode = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.BusOnly, 30, new SafetyExtra.Distance(11.0), null));

    assertEquals("fixed_camera", fixed.kind);
    assertEquals(60, fixed.speedKph);
    assertEquals(155.5, fixed.distanceM, 0.0);
    assertEquals("mobile_camera", mobile.kind);
    assertEquals("section_camera", section.kind);
    assertEquals("speed_bump", bump.kind);
    assertEquals(25.0, bump.distanceM, 0.0);
    assertFalse(invalidSpeed.present);
    assertFalse(ignoredSchoolZone.present);
    assertFalse(ignoredCode.present);
  }

  @Test
  void safetySourceMapsCodeAndDistanceImmediately() {
    Naver6805ObjectMapper.SafetySource source = Naver6805ObjectMapper.mapSafetySource(
        guidanceSafety(SafetyCode.SpeedBump, 37.5));

    assertTrue(source.present);
    assertEquals("SpeedBump", source.codeName);
    assertTrue(source.speedBump);
    assertEquals(37.5, source.distanceM, 0.0);
    assertEquals("safety_source_ok", source.outcome);
  }

  @Test
  void finalBumpRequiresMatchingValidSourceSnapshot() {
    SafeControlItem bump = safety(SafetyCode.SpeedBump, null, null, null);
    Naver6805ObjectMapper.SafetySource source = Naver6805ObjectMapper.mapSafetySource(
        guidanceSafety(SafetyCode.SpeedBump, 37.5));

    Naver6805ObjectMapper.Safety mapped = Naver6805ObjectMapper.mapSafety(bump, source);
    assertTrue(mapped.present);
    assertEquals("speed_bump", mapped.kind);
    assertEquals(37.5, mapped.distanceM, 0.0);
    assertFalse(Naver6805ObjectMapper.mapSafety(
        bump, Naver6805ObjectMapper.SafetySource.absent()).present);
    assertFalse(Naver6805ObjectMapper.mapSafety(
        bump, new Naver6805ObjectMapper.SafetySource(
            true, "SpeedCam", false, 37.5, "safety_source_ok")).present);
  }

  @Test
  void safetySourceRejectsNullWrongCodeInvalidDistanceAndExceptionsWithoutPoisoning() {
    assertFalse(Naver6805ObjectMapper.mapSafetySource(null).present);
    assertFalse(Naver6805ObjectMapper.mapSafetySource(
        guidanceSafety(SafetyCode.SpeedCam, 37.5)).present);
    assertFalse(Naver6805ObjectMapper.mapSafetySource(
        guidanceSafety(SafetyCode.SpeedBump, 0.0)).present);
    assertFalse(Naver6805ObjectMapper.mapSafetySource(
        guidanceSafety(SafetyCode.SpeedBump, Double.NaN)).present);
    assertFalse(Naver6805ObjectMapper.mapSafetySource(
        guidanceSafety(SafetyCode.SpeedBump, Double.POSITIVE_INFINITY)).present);
    assertFalse(Naver6805ObjectMapper.mapSafetySource(new GuidanceSafety() {
      @Override
      public SafetyCode getCode() {
        throw new IllegalStateException("test-only");
      }

      @Override
      public double distance() {
        return 37.5;
      }
    }).present);

    assertTrue(Naver6805ObjectMapper.mapSafetySource(
        guidanceSafety(SafetyCode.SpeedBump, 25.0)).present);
  }

  @Test
  void safetyDoesNotEagerlyCallIrrelevantExtrasOrClassifiers() {
    try {
      SafetyCode.setThrowingAllSpeedCamerasForTesting(true);
      assertSafetyKind("speed_bump", new SafeControlItem(
          new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null),
          new SafetyExtra.Distance(25.0), null, false, false, true));
      assertSafetyKind("mobile_camera", new SafeControlItem(
          new SafetySign(new SafetySign.SignType.Safety(SafetyCode.MoveSpeedCam), 70),
          new SafetyExtra.Distance(80.0), null, false, false, true));
      assertSafetyKind("section_camera", new SafeControlItem(
          new SafetySign(new SafetySign.SignType.Safety(SafetyCode.StartSectionSpeedCam), 80),
          null, new SafetyExtra.Distance(300.0), false, true, false));
    } finally {
      SafetyCode.setThrowingAllSpeedCamerasForTesting(false);
    }

    assertSafetyKind("fixed_camera", new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedCam), 60),
        new SafetyExtra.Distance(155.5), null, false, false, true));

    try {
      SafetyCode.setThrowingSpeedBumpForTesting(true);
      assertFalse(Naver6805ObjectMapper.mapSafety(safety(
          SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(100.0), null)).present);
    } finally {
      SafetyCode.setThrowingSpeedBumpForTesting(false);
    }
    assertTrue(Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(100.0), null)).present);
  }

  @Test
  void successfulMethodLookupsAreCachedButFailuresAreNotCached() {
    Naver6805ObjectMapper.clearMethodCacheForTesting();
    assertEquals(0, Naver6805ObjectMapper.methodCacheSizeForTesting());

    Naver6805ObjectMapper.Safety first = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(155.5), null));
    int afterFirst = Naver6805ObjectMapper.methodCacheSizeForTesting();
    Naver6805ObjectMapper.Safety second = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(160.0), null));
    int afterSecond = Naver6805ObjectMapper.methodCacheSizeForTesting();

    assertTrue(first.present);
    assertTrue(second.present);
    assertTrue(afterFirst > 0);
    assertEquals(afterFirst, afterSecond);

    assertFalse(Naver6805ObjectMapper.invokeForTesting(
        new Object(), "java.lang.Object", "missingProductionAccessor", Object.class)
        .isPresent());
    assertEquals(afterSecond, Naver6805ObjectMapper.methodCacheSizeForTesting());
  }

  @Test
  void safetyNestedFailuresAreIsolatedAtSignTypeCodeExtraAndDistanceStages() {
    Naver6805ObjectMapper.resetDiagnosticsForTesting();

    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedCam), 60),
        new SafetyExtra.Distance(10.0), null, true, false, false));
    assertIsolatedSafetyFailure(new SafeControlItem(
        null, new SafetyExtra.Distance(10.0), null));
    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedCam), 60, true, false),
        new SafetyExtra.Distance(10.0), null));
    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(null, 60), new SafetyExtra.Distance(10.0), null));
    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Other(), 60),
        new SafetyExtra.Distance(10.0), null));
    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedCam, true), 60),
        new SafetyExtra.Distance(10.0), null));
    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(null), 60),
        new SafetyExtra.Distance(10.0), null));
    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedCam), 60),
        new SafetyExtra.Distance(10.0), null, false, true, false));
    assertIsolatedSafetyFailure(new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.StartSectionSpeedCam), 60),
        null, new SafetyExtra.Distance(10.0), false, false, true));
    assertIsolatedSafetyFailure(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.SchoolZone(10.0), null));
    assertIsolatedSafetyFailure(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(0.0), null));
    assertIsolatedSafetyFailure(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(10.0, true), null));

    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("safety_sign_descriptor"));
    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("safety_type_descriptor"));
    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("safety_code_descriptor"));
    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("safety_distance_descriptor"));
    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("safety_distance"));
    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("safety_distance_accessor"));
  }

  @Test
  void lifecycleRecognizesOnlyExactRuntimeStatusSubtypes() {
    assertEquals("guiding", Naver6805ObjectMapper.mapLifecycle(
        new NaviStatusBroadcaster.Status.Guiding()));
    assertEquals("stopped", Naver6805ObjectMapper.mapLifecycle(
        new NaviStatusBroadcaster.Status.Stopped()));
    assertEquals("arrived", Naver6805ObjectMapper.mapLifecycle(
        new NaviStatusBroadcaster.Status.Arrived()));
    assertEquals("unknown", Naver6805ObjectMapper.mapLifecycle(new Object()));
  }

  @Test
  void publicChannelMapperReturnsOptionalUpdatesAndRejectsUnknownChannels() {
    Optional<Naver6805ObjectMapper.MappedUpdate> update = Naver6805ObjectMapper.map(
        "tbt_current", tbt(30.0, TurnPointType.Left, "좌회전", "도로"));
    Optional<Naver6805ObjectMapper.MappedUpdate> invalidKnown = Naver6805ObjectMapper.map(
        "tbt_current", tbt(30.0, TurnPointType.Rest, "휴게소", "도로"));
    Optional<Naver6805ObjectMapper.MappedUpdate> route = Naver6805ObjectMapper.map(
        "route", new Object());
    Optional<Naver6805ObjectMapper.MappedUpdate> unknown = Naver6805ObjectMapper.map(
        "lane", new Object());

    assertTrue(update.isPresent());
    assertEquals("tbt_current", update.get().channel);
    assertTrue(update.get().guidance.present);
    assertTrue(invalidKnown.isPresent());
    assertEquals("tbt_current", invalidKnown.get().channel);
    assertFalse(invalidKnown.get().guidance.present);
    assertTrue(route.isPresent());
    assertEquals("route", route.get().channel);
    assertFalse(route.get().route.present);
    assertFalse(unknown.isPresent());
  }

  @Test
  void nestedAccessorFailureInvalidatesOnlyThatItemAndDiagnosticsAreBounded() {
    Naver6805ObjectMapper.resetDiagnosticsForTesting();
    Naver6805ObjectMapper.Safety throwingDistance = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(30.0, true), null));
    Naver6805ObjectMapper.Guidance stillValidGuidance = Naver6805ObjectMapper.mapGuidance(
        tbt(15.0, TurnPointType.Right, "우회전", "도로"));

    for (int i = 0; i < 128; i++) {
      Naver6805ObjectMapper.mapSafety(safety(
          SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(i, true), null));
    }

    assertFalse(throwingDistance.present);
    assertTrue(stillValidGuidance.present);
    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes().size() <= 32);
    assertTrue(Naver6805ObjectMapper.diagnosticsForTesting().snapshotCodes()
        .contains("safety_distance_accessor"));
  }

  @Test
  void sectionCamerasRequireSectionExtraDistanceAndDoNotUseGeneralExtra() {
    Naver6805ObjectMapper.Safety missingSectionDistance = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.StartSectionSpeedCam, 80, new SafetyExtra.Distance(10.0), null));
    Naver6805ObjectMapper.Safety section = Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.StartSectionSpeedCam, 80,
        new SafetyExtra.Distance(10.0), new SafetyExtra.Distance(500.0)));

    assertFalse(missingSectionDistance.present);
    assertTrue(section.present);
    assertEquals("section_camera", section.kind);
    assertEquals(500.0, section.distanceM, 0.0);
  }

  private static TbtItem tbt(
      double distanceM, TurnPointType type, String mainText, String roadName) {
    return new TbtItem(distanceM, new TbtDataItem(type, mainText, roadName));
  }

  private static CurrentRoute currentRoute(List<?> points) {
    return new CurrentRoute(new NaviRouteData(new RouteInfoImpl(points)));
  }

  private static SafeControlItem safety(
      SafetyCode code, Integer speedKph, SafetyExtra extra, SafetyExtra sectionExtra) {
    return new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(code), speedKph), extra, sectionExtra);
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

  private static void assertGuidanceFailureDoesNotPoisonNextValid(TbtItem item) {
    assertFalse(Naver6805ObjectMapper.mapGuidance(item).present);
    assertTrue(Naver6805ObjectMapper.mapGuidance(
        tbt(15.0, TurnPointType.Right, "우회전", "도로")).present);
  }

  private static void assertSafetyKind(String kind, SafeControlItem item) {
    Naver6805ObjectMapper.Safety safety = Naver6805ObjectMapper.mapSafety(item);
    assertTrue(safety.present);
    assertEquals(kind, safety.kind);
  }

  private static void assertIsolatedSafetyFailure(SafeControlItem item) {
    assertFalse(Naver6805ObjectMapper.mapSafety(item).present);
    assertTrue(Naver6805ObjectMapper.mapSafety(safety(
        SafetyCode.SpeedCam, 60, new SafetyExtra.Distance(100.0), null)).present);
  }
}

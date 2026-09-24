package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.naver.map.core.common.model.SafeControlItem;
import com.naver.map.core.common.model.SafetySign;
import com.naver.map.core.navigation.NaviStatusBroadcaster;
import com.naver.map.core.navigation.model.CurrentRoute;
import com.naver.map.core.navigation.model.NaviRouteData;
import com.naver.maps.geometry.LatLng;
import com.naver.maps.navi.v2.api.guidance.model.GuidanceSafety;
import com.naver.maps.navi.v2.shared.api.route.constants.SafetyCode;
import com.naver.maps.navi.v2.shared.api.route.model.RouteInfoImpl;
import java.util.List;
import org.junit.jupiter.api.Test;

final class FieldAcceptanceMappingOutcomeTest {
  @Test
  void routeSuccessAndRejectionEncodeOnlyTheExactScalarSchema() {
    ProductionRuntime runtime = runtime();
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    runtime.onRoute(new CurrentRoute(new NaviRouteData(new RouteInfoImpl(List.of(
        new LatLng(37.5, 127.1), new LatLng(37.6, 127.2))))));

    ProductionRuntime.MappingOutcome success = take(runtime);
    assertEquals("route", success.channel);
    assertEquals("route_ok", success.result);
    assertEquals(2, success.inputCount);
    assertEquals(2, success.outputCount);
    assertEquals(1L, success.revision);
    assertTrue(success.itemPresent);
    assertFalse(success.distanceValid);
    assertTrue(success.frameEligible);
    String encoded = encode("route", success);
    assertExactScalarKeys(encoded);
    assertFalse(encoded.contains("latitude"), encoded);
    assertFalse(encoded.contains("longitude"), encoded);
    assertFalse(encoded.contains("37.5"), encoded);
    assertFalse(encoded.contains("127.1"), encoded);
    assertFalse(encoded.contains("destination"), encoded);
    assertFalse(encoded.contains("toString"), encoded);
    assertNull(runtime.takeMappingOutcome());

    runtime.onRoute(null);
    ProductionRuntime.MappingOutcome rejected = take(runtime);
    assertEquals("route_root_descriptor", rejected.result);
    assertFalse(rejected.itemPresent);
    assertEquals(0, rejected.inputCount);
    assertEquals(0, rejected.outputCount);
  }

  @Test
  void bumpSourceFinalSuccessAndFinalRejectionEncodeOnlyScalars() {
    ProductionRuntime runtime = runtime();
    runtime.onStatus(new NaviStatusBroadcaster.Status.Guiding());
    SafeControlItem finalBump = new SafeControlItem(
        new SafetySign(new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null),
        null, null);

    runtime.onSafetySource(guidanceSafety(SafetyCode.SpeedBump, 37.5));
    ProductionRuntime.MappingOutcome source = take(runtime);
    assertEquals("safety_source_ok", source.result);
    assertTrue(source.itemPresent);
    assertTrue(source.distanceValid);
    assertExactScalarKeys(encode("safety", source));

    runtime.onSafety(finalBump);
    ProductionRuntime.MappingOutcome success = take(runtime);
    assertEquals("safety_ok", success.result);
    assertTrue(success.itemPresent);
    assertTrue(success.distanceValid);
    assertEquals(1L, success.revision);
    String encoded = encode("safety", success);
    assertExactScalarKeys(encoded);
    assertFalse(encoded.contains("37.5"), encoded);
    assertFalse(encoded.contains("exception"), encoded);
    assertFalse(encoded.contains("message"), encoded);

    runtime.onSafety(finalBump);
    ProductionRuntime.MappingOutcome rejected = take(runtime);
    assertEquals("safety_rejected", rejected.result);
    assertFalse(rejected.itemPresent);
    assertFalse(rejected.distanceValid);
  }

  @Test
  void routeAndSafetyChannelsRejectRawObjects() {
    BoundedMappingDiagnostics diagnostics = new BoundedMappingDiagnostics(4096);
    assertThrows(IllegalArgumentException.class,
        () -> diagnostics.encode("route", new Object(), 0L));
    assertThrows(IllegalArgumentException.class,
        () -> diagnostics.encode("safety", new Object(), 0L));
  }

  private static ProductionRuntime runtime() {
    return new ProductionRuntime(
        new NaverNavigationAggregator(new NaverNavigationAggregator.FixedSessionIds(
            "77777777-7777-4777-8777-777777777777")),
        NaverNavigationSender.noopForTesting(),
        true);
  }

  private static ProductionRuntime.MappingOutcome take(ProductionRuntime runtime) {
    Object value = runtime.takeMappingOutcome();
    assertTrue(value instanceof ProductionRuntime.MappingOutcome);
    return (ProductionRuntime.MappingOutcome) value;
  }

  private static String encode(String channel, Object value) {
    return new BoundedMappingDiagnostics(4096).encode(channel, value, 0L).encodedJson();
  }

  private static void assertExactScalarKeys(String encoded) {
    for (String key : new String[] {
        "channel", "result", "root_descriptor", "input_count", "output_count",
        "revision", "item_present", "distance_valid", "frame_eligible"}) {
      assertTrue(encoded.contains("\"" + key + "\":"), encoded);
    }
    assertFalse(encoded.contains("\"accessors\""), encoded);
    assertFalse(encoded.contains("\"numeric_bucket\""), encoded);
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
}

package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.naver.map.core.common.model.SafeControlItem;
import com.naver.map.core.common.model.SafetyExtra;
import com.naver.map.core.common.model.SafetySign;
import com.naver.maps.navi.v2.shared.api.route.constants.SafetyCode;
import org.junit.jupiter.api.Test;

final class FieldAcceptanceNestedSafetyTest {
  @Test
  void oneRealShapeFixtureFeedsBothBumpEvidenceAndProductionMapping() {
    SafeControlItem fixture = new SafeControlItem(
        new SafetySign(
            new SafetySign.SignType.Safety(SafetyCode.SpeedBump), null),
        new SafetyExtra.Distance(25.0),
        null);

    Naver6805ObjectMapper.Safety mapped = Naver6805ObjectMapper.mapSafety(fixture);

    assertThrows(IllegalArgumentException.class,
        () -> new BoundedMappingDiagnostics(4096).encode("safety", fixture, 0L));
    assertTrue(mapped.present);
    assertEquals("speed_bump", mapped.kind);
    assertEquals(25.0, mapped.distanceM, 0.0);
  }
}

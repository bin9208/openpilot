package ai.comma.naver.dexpatch;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

final class FieldAcceptanceProfileContractTest {
  private static final String FIELD_HOOKS =
      "Lai/comma/naver/payload/FieldAcceptanceHooks;";

  @Test
  void generatedFieldRulesRetargetAllSevenNavigationAndInfrastructureAnchors() {
    List<PatchRule> navigation = Arrays.stream(
        DiagnosticProfileContract.encodedNavigationPatchRules())
        .map(DexPatchMain::decodePatchRule)
        .toList();
    PatchRule infrastructure = DexPatchMain.decodePatchRule(
        DiagnosticProfileContract.encodedInfrastructurePatchRule());

    assertEquals("6.8.0.5", DiagnosticProfileContract.PROFILE_ID);
    assertEquals(FIELD_HOOKS, DiagnosticProfileContract.HOOK_CLASS_DESCRIPTOR);
    assertEquals("naver-6.8.0.5-field-acceptance-v4",
        DiagnosticProfileContract.PAYLOAD_BUILD_ID);
    assertEquals(Map.of(
        "status", "onStatus",
        "tbt_current", "onCurrentTbt",
        "tbt_next", "onNextTbt",
        "safety_source", "onSafetySource",
        "safety", "onSafety",
        "route", "onRoute",
        "lane", "onLane"),
        navigation.stream().collect(Collectors.toMap(
            rule -> rule.anchor().name(),
            rule -> rule.hook().getName())));
    assertEquals(7, navigation.size());
    navigation.forEach(rule -> assertEquals(
        FIELD_HOOKS, rule.hook().getDefiningClass()));
    assertEquals("capture_export_quiesce", infrastructure.anchor().name());
    assertEquals("beforeCaptureRead", infrastructure.hook().getName());
    assertEquals(FIELD_HOOKS, infrastructure.hook().getDefiningClass());
  }

  @Test
  void canonicalProfileRemainsDiagnosticAndDefinesSevenExactOneAnchors()
      throws Exception {
    String profile = Files.readString(Path.of("../profiles/6.8.0.5.json"));

    assertEquals(8, count(profile, "\"expected_count\": 1"));
    assertEquals(1, count(profile,
        "\"hook_class_descriptor\": "
            + "\"Lai/comma/naver/payload/DiagnosticHooks;\""));
    assertFalse(profile.contains("FieldAcceptanceHooks"));
  }

  private static int count(String text, String token) {
    return text.split(java.util.regex.Pattern.quote(token), -1).length - 1;
  }
}

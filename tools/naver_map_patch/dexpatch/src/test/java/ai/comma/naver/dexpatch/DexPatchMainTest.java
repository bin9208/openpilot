package ai.comma.naver.dexpatch;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

final class DexPatchMainTest {
  @Test
  void decodesExactRule_whenAllMetadataFieldsArePresent() {
    // Given: one URL-safe rule argument produced by the Python inspector.
    String raw = String.join(Character.toString(0x1f), List.of(
        "status", "classes7.dex", "Lsample/Target;", "emit()V", "return-void"));
    String encoded = Base64.getUrlEncoder().withoutPadding()
        .encodeToString(raw.getBytes(StandardCharsets.UTF_8));

    // When: the Java boundary decodes it.
    AnchorRule rule = DexPatchMain.decodeRule(encoded);

    // Then: every exact anchor field survives without fuzzy parsing.
    assertEquals(
        new AnchorRule("status", "classes7.dex", "Lsample/Target;", "emit()V", List.of("return-void")),
        rule);
  }

  @Test
  void rejectsRule_whenInstructionWindowIsMissing() {
    // Given: an encoded rule containing identity fields but no instruction token.
    String raw = String.join(
        Character.toString(0x1f), List.of("status", "classes7.dex", "Lsample/Target;", "emit()V"));
    String encoded = Base64.getUrlEncoder().withoutPadding()
        .encodeToString(raw.getBytes(StandardCharsets.UTF_8));

    // When / Then: the boundary refuses the malformed rule.
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.decodeRule(encoded));
  }

  @Test
  void decodesExactDiagnosticHook_whenProfileMetadataIsComplete() {
    // Given: one patch rule encoded from a profile anchor and its callback metadata.
    String raw = String.join(Character.toString(0x1f), List.of(
        "route", "classes11.dex", "Lsample/Target;", "emit(Ljava/lang/Object;)V", "1",
        "return-void", "Lai/comma/naver/payload/DiagnosticHooks;",
        "onRoute(Ljava/lang/Object;)V", "method_entry", "0", "parameter", "0"));
    String encoded = Base64.getUrlEncoder().withoutPadding()
        .encodeToString(raw.getBytes(StandardCharsets.UTF_8));

    // When: the Java patch boundary decodes the profile-owned rule.
    PatchRule rule = DexPatchMain.decodePatchRule(encoded);

    // Then: exact identity, placement, argument source, and callback survive intact.
    assertEquals("route", rule.anchor().name());
    assertEquals(HookPlacement.methodEntry(), rule.placement());
    assertEquals(HookArgument.parameter(0), rule.argument());
    assertEquals("onRoute", rule.hook().getName());
  }

  @Test
  void productionInventoryRequiresExactClassesAndPublicBetaBuildId() {
    Set<String> exactTypes = Set.of(
        "Lai/comma/naver/payload/Naver6805ObjectMapper;",
        "Lai/comma/naver/payload/NaverNavigationAggregator;",
        "Lai/comma/naver/payload/NaverNavigationEnvelope;",
        "Lai/comma/naver/payload/NaverNavigationSender;",
        "Lai/comma/naver/payload/NaverNavigationState;",
        "Lai/comma/naver/payload/ProductionHooks;",
        "Lai/comma/naver/payload/ProductionRuntime;");
    Set<String> references = Set.of(
        "naver-6.8.0.5-public-beta-v2", "ProductionHooks", "CurrentRoute");

    DexPatchMain.verifyProductionInventory(
        exactTypes, references, "naver-6.8.0.5-public-beta-v2");
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        Set.of("Lai/comma/naver/payload/DiagnosticRuntime;"), references,
        "naver-6.8.0.5-public-beta-v2"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-public-beta-v1", "ProductionHooks"),
        "naver-6.8.0.5-public-beta-v1"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-production-v7"),
        "naver-6.8.0.5-production-v7"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-public-beta-v2", "naver-6.8.0.5-production-v8"),
        "naver-6.8.0.5-public-beta-v2"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, references, "naver-6.8.0.5-production-v8"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-public-beta-v2", "SIMULATION_ENABLED"),
        "naver-6.8.0.5-public-beta-v2"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-public-beta-v2", "naver-6.8.0.5-diagnostic-offline-v3"),
        "naver-6.8.0.5-public-beta-v2"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-public-beta-v2", "naver-6.8.0.5-field-acceptance-v2"),
        "naver-6.8.0.5-public-beta-v2"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-public-beta-v2", "naver-6.8.0.5-field-acceptance-v3"),
        "naver-6.8.0.5-public-beta-v2"));
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes, Set.of("naver-6.8.0.5-public-beta-v2", "naver-6.8.0.5-field-acceptance-v4"),
        "naver-6.8.0.5-public-beta-v2"));
  }

  @Test
  void productionInventoryRejectsFieldAcceptanceHookReference() {
    Set<String> exactTypes = Set.of(
        "Lai/comma/naver/payload/Naver6805ObjectMapper;",
        "Lai/comma/naver/payload/NaverNavigationAggregator;",
        "Lai/comma/naver/payload/NaverNavigationEnvelope;",
        "Lai/comma/naver/payload/NaverNavigationSender;",
        "Lai/comma/naver/payload/NaverNavigationState;",
        "Lai/comma/naver/payload/ProductionHooks;",
        "Lai/comma/naver/payload/ProductionRuntime;");

    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionInventory(
        exactTypes,
        Set.of(
            "naver-6.8.0.5-public-beta-v2",
            "Lai/comma/naver/payload/FieldAcceptanceHooks;->onStatus(Ljava/lang/Object;)V"),
        "naver-6.8.0.5-public-beta-v2"));
  }

  @Test
  void productionBindingCountsRequireEachExactTargetOnce() {
    Map<String, Integer> counts = new LinkedHashMap<>();
    counts.put("status", 1);
    counts.put("tbt_current", 1);
    counts.put("tbt_next", 1);
    counts.put("safety_source", 1);
    counts.put("safety", 1);
    counts.put("route", 1);

    DexPatchMain.verifyProductionBindingCounts(counts);
    counts.put("status", 2);
    assertThrows(IllegalArgumentException.class, () -> DexPatchMain.verifyProductionBindingCounts(counts));
  }

  @Test
  void globalHookInventoryRejectsDuplicateUnexpectedDiagnosticAndFieldAcceptanceCalls() {
    Map<String, Integer> exact = new LinkedHashMap<>();
    exact.put("onStatus(Ljava/lang/Object;)V", 1);
    exact.put("onCurrentTbt(Ljava/lang/Object;)V", 1);
    exact.put("onNextTbt(Ljava/lang/Object;)V", 1);
    exact.put("onSafetySource(Ljava/lang/Object;)V", 1);
    exact.put("onSafety(Ljava/lang/Object;)V", 1);
    exact.put("onRoute(Ljava/lang/Object;)V", 1);

    DexPatchMain.verifyGlobalProductionHookReferences(exact, 0, 0);
    exact.put("onStatus(Ljava/lang/Object;)V", 2);
    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyGlobalProductionHookReferences(exact, 0, 0));
    exact.put("onStatus(Ljava/lang/Object;)V", 1);
    exact.put("onLane(Ljava/lang/Object;)V", 1);
    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyGlobalProductionHookReferences(exact, 0, 0));
    exact.remove("onLane(Ljava/lang/Object;)V");
    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyGlobalProductionHookReferences(exact, 1, 0));
    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyGlobalProductionHookReferences(exact, 0, 1));
  }
}

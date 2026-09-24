package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

final class FieldAcceptanceGradleContractTest {
  @Test
  void diagnosticBuildDefinesTheExplicitFieldAcceptanceVariant() throws Exception {
    String build = Files.readString(Path.of("build.gradle"));
    String diagnostic = Files.readString(Path.of("diagnostic-payload.gradle"));

    assertTrue(build.contains("fieldAcceptance"));
    assertTrue(diagnostic.contains("src/fieldAcceptance/java"));
    assertTrue(diagnostic.contains("naver-6.8.0.5-field-acceptance-v4"));
    assertTrue(diagnostic.contains("Lai/comma/naver/payload/FieldAcceptanceHooks;"));
    assertTrue(diagnostic.contains("fieldAcceptanceSourceNames"));
    assertTrue(diagnostic.contains("productionFamilySourceNames"));
  }

  @Test
  void fieldExportHookKeepsTheApprovedUriAndBoundedQuiesceContract()
      throws Exception {
    String source = Files.readString(Path.of(
        "src/fieldAcceptance/java/ai/comma/naver/payload/FieldAcceptanceHooks.java"));
    int uriCheck = source.indexOf("AndroidCaptureSharing.isApprovedUri(value)");
    int quiesce = source.indexOf("diagnosticRuntime.quiesceForExtraction(");
    int timeout = source.indexOf("DiagnosticConfig.CAPTURE_QUIESCE_TIMEOUT_MS");
    int failure = source.indexOf("throw new IllegalStateException(");

    assertTrue(uriCheck >= 0);
    assertTrue(uriCheck < quiesce);
    assertTrue(quiesce < timeout);
    assertTrue(timeout < failure);
    assertFalse(source.contains("publishSnapshot("));
  }
}

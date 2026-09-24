package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.android.tools.smali.dexlib2.DexFileFactory;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.dexbacked.DexBackedDexFile;
import com.android.tools.smali.dexlib2.iface.ClassDef;
import java.io.File;
import java.util.HashSet;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

final class FieldAcceptancePayloadDexTest {
  private static final String FIELD_BUILD_ID =
      "naver-6.8.0.5-field-acceptance-v4";

  @Test
  void fieldDexContainsBothRuntimeFamiliesAndOnlyTheFieldHookMarker() throws Exception {
    var dex = DexFileFactory.loadDexFile(
        new File(System.getProperty("naver.fieldAcceptance.payloadDex")),
        Opcodes.getDefault());
    Set<String> topLevelTypes = dex.getClasses().stream()
        .map(ClassDef::getType)
        .map(type -> type.indexOf('$') >= 0
            ? type.substring(0, type.indexOf('$')) + ";" : type)
        .collect(Collectors.toSet());

    assertEquals(Set.of(
        "Lai/comma/naver/payload/AndroidApplicationFiles;",
        "Lai/comma/naver/payload/AndroidCaptureSharing;",
        "Lai/comma/naver/payload/AndroidProcess;",
        "Lai/comma/naver/payload/AndroidSQLiteCaptureDatabase;",
        "Lai/comma/naver/payload/AsyncDiagnosticSink;",
        "Lai/comma/naver/payload/BoundedMappingDiagnostics;",
        "Lai/comma/naver/payload/CaptureDatabase;",
        "Lai/comma/naver/payload/CaptureRetentionPolicy;",
        "Lai/comma/naver/payload/DiagnosticConfig;",
        "Lai/comma/naver/payload/DiagnosticCounters;",
        "Lai/comma/naver/payload/DiagnosticFrameSink;",
        "Lai/comma/naver/payload/DiagnosticRuntime;",
        "Lai/comma/naver/payload/DiagnosticSampler;",
        "Lai/comma/naver/payload/EncodedDiagnosticFrame;",
        "Lai/comma/naver/payload/FieldAcceptanceHooks;",
        "Lai/comma/naver/payload/FrameBatchConsumer;",
        "Lai/comma/naver/payload/HookRuntime;",
        "Lai/comma/naver/payload/Naver6805ObjectMapper;",
        "Lai/comma/naver/payload/NaverNavigationAggregator;",
        "Lai/comma/naver/payload/NaverNavigationEnvelope;",
        "Lai/comma/naver/payload/NaverNavigationSender;",
        "Lai/comma/naver/payload/NaverNavigationState;",
        "Lai/comma/naver/payload/NavigationTransport;",
        "Lai/comma/naver/payload/NetworkFrameConsumer;",
        "Lai/comma/naver/payload/OfflineCaptureStore;",
        "Lai/comma/naver/payload/PersistentNavigationTransport;",
        "Lai/comma/naver/payload/ProductionRuntime;"), topLevelTypes);
    assertFalse(topLevelTypes.contains(
        "Lai/comma/naver/payload/DiagnosticHooks;"));
    assertFalse(topLevelTypes.contains(
        "Lai/comma/naver/payload/ProductionHooks;"));
  }

  @Test
  void fieldDexCarriesOneUnambiguousBuildIdentity() throws Exception {
    var dex = DexFileFactory.loadDexFile(
        new File(System.getProperty("naver.fieldAcceptance.payloadDex")),
        Opcodes.getDefault());
    Set<String> strings = new HashSet<>();
    if (dex instanceof DexBackedDexFile backed) {
      backed.getStringReferences().forEach(reference -> strings.add(reference.getString()));
    }

    assertEquals(FIELD_BUILD_ID, DiagnosticConfig.OFFLINE_CAPTURE_PAYLOAD_BUILD_ID);
    assertTrue(strings.contains(FIELD_BUILD_ID));
    assertFalse(strings.contains("naver-6.8.0.5-diagnostic-offline-v3"));
    assertFalse(strings.contains("naver-6.8.0.5-field-acceptance-v1"));
    assertFalse(strings.contains("naver-6.8.0.5-field-acceptance-v2"));
    assertFalse(strings.contains("naver-6.8.0.5-field-acceptance-v3"));
    assertFalse(strings.contains("naver-6.8.0.5-production-v8"));
  }
}

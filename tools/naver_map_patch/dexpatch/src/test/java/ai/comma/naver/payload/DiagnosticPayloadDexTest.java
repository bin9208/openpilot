package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.android.tools.smali.dexlib2.DexFileFactory;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.iface.ClassDef;
import com.android.tools.smali.dexlib2.iface.Method;
import com.android.tools.smali.dexlib2.iface.instruction.Instruction;
import com.android.tools.smali.dexlib2.iface.instruction.ReferenceInstruction;
import java.io.File;
import java.util.HashSet;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

final class DiagnosticPayloadDexTest {
  @Test
  void builtPayloadDexContainsOnlyDiagnosticPayloadClasses() throws Exception {
    String payloadDex = System.getProperty("naver.diagnostic.payloadDex");
    Set<String> types = DexFileFactory.loadDexFile(new File(payloadDex), Opcodes.getDefault())
        .getClasses().stream().map(classDef -> classDef.getType()).collect(Collectors.toSet());

    Set<String> topLevelTypes = types.stream()
        .map(type -> type.indexOf('$') >= 0 ? type.substring(0, type.indexOf('$')) + ";" : type)
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
        "Lai/comma/naver/payload/DiagnosticHooks;",
        "Lai/comma/naver/payload/DiagnosticRuntime;",
        "Lai/comma/naver/payload/DiagnosticSampler;",
        "Lai/comma/naver/payload/EncodedDiagnosticFrame;",
        "Lai/comma/naver/payload/FrameBatchConsumer;",
        "Lai/comma/naver/payload/HookRuntime;",
        "Lai/comma/naver/payload/NavigationTransport;",
        "Lai/comma/naver/payload/NetworkFrameConsumer;",
        "Lai/comma/naver/payload/OfflineCaptureStore;",
        "Lai/comma/naver/payload/PersistentNavigationTransport;"), topLevelTypes);
  }

  @Test
  void diagnosticDexContainsAndroidSqliteReferences() throws Exception {
    Set<String> references = allDexReferences();
    assertTrue(references.stream()
        .anyMatch(value -> value.startsWith("Landroid/database/sqlite/")));
    assertTrue(references.stream()
        .anyMatch(value -> value.startsWith("Landroid/database/DatabaseErrorHandler;")));
    assertFalse(references.stream()
        .anyMatch(value -> value.startsWith("Landroid/database/DefaultDatabaseErrorHandler;")));
  }

  private static Set<String> allDexReferences() throws Exception {
    String payloadDex = System.getProperty("naver.diagnostic.payloadDex");
    Set<String> references = new HashSet<>();
    for (ClassDef classDef :
        DexFileFactory.loadDexFile(new File(payloadDex), Opcodes.getDefault()).getClasses()) {
      references.add(classDef.getType());
      if (classDef.getSuperclass() != null) {
        references.add(classDef.getSuperclass());
      }
      references.addAll(classDef.getInterfaces());
      classDef.getFields().forEach(field -> references.add(field.getType()));
      for (Method method : classDef.getMethods()) {
        references.add(method.getReturnType());
        method.getParameterTypes().forEach(type -> references.add(type.toString()));
        if (method.getImplementation() == null) {
          continue;
        }
        for (Instruction instruction : method.getImplementation().getInstructions()) {
          if (instruction instanceof ReferenceInstruction) {
            references.add(((ReferenceInstruction) instruction).getReference().toString());
          }
        }
      }
    }
    return references;
  }
}

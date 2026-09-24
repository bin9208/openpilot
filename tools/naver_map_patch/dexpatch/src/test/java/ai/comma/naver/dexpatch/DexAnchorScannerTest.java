package ai.comma.naver.dexpatch;

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.android.tools.smali.dexlib2.Opcode;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.iface.DexFile;
import com.android.tools.smali.dexlib2.immutable.ImmutableClassDef;
import com.android.tools.smali.dexlib2.immutable.ImmutableDexFile;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethod;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodImplementation;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction10x;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction21c;
import com.android.tools.smali.dexlib2.immutable.reference.ImmutableStringReference;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

final class DexAnchorScannerTest {
  @Test
  void countsOneAnchor_whenExactDexClassMethodAndWindowMatch() {
    // Given: one exact method with one exact two-instruction diagnostic window.
    DexUnit unit = new DexUnit("classes7.dex", dexWithTokens(List.of("status-marker")));
    AnchorRule rule = rule(List.of("const-string \"status-marker\"", "return-void"));

    // When: the scanner evaluates the exact rule.
    Map<String, Integer> counts = DexAnchorScanner.scan(List.of(unit), List.of(rule));

    // Then: exactly one window is reported.
    assertEquals(Map.of("status", 1), counts);
  }

  @Test
  void countsZeroAnchors_whenInstructionWindowDiffers() {
    // Given: the exact dex/class/method but a different instruction reference.
    DexUnit unit = new DexUnit("classes7.dex", dexWithTokens(List.of("other-marker")));
    AnchorRule rule = rule(List.of("const-string \"status-marker\"", "return-void"));

    // When: the scanner evaluates the exact rule.
    Map<String, Integer> counts = DexAnchorScanner.scan(List.of(unit), List.of(rule));

    // Then: the rule has no match.
    assertEquals(Map.of("status", 0), counts);
  }

  @Test
  void countsDuplicateAnchors_whenWindowOccursTwice() {
    // Given: the exact method repeats the full one-instruction marker window.
    DexUnit unit = new DexUnit("classes7.dex", dexWithTokens(List.of("status-marker", "status-marker")));
    AnchorRule rule = rule(List.of("const-string \"status-marker\""));

    // When: the scanner evaluates the exact rule.
    Map<String, Integer> counts = DexAnchorScanner.scan(List.of(unit), List.of(rule));

    // Then: both occurrences are visible so the caller can fail closed.
    assertEquals(Map.of("status", 2), counts);
  }

  private static AnchorRule rule(List<String> window) {
    return new AnchorRule("status", "classes7.dex", "Lsample/Target;", "emit()V", window);
  }

  private static DexFile dexWithTokens(List<String> tokens) {
    var instructions = tokens.stream()
        .map(token -> new ImmutableInstruction21c(
            Opcode.CONST_STRING, 0, new ImmutableStringReference(token)))
        .collect(java.util.stream.Collectors.<com.android.tools.smali.dexlib2.iface.instruction.Instruction>toList());
    instructions.add(new ImmutableInstruction10x(Opcode.RETURN_VOID));
    var implementation = new ImmutableMethodImplementation(1, instructions, List.of(), List.of());
    var method = new ImmutableMethod(
        "Lsample/Target;", "emit", List.of(), "V", 0, Set.of(), Set.of(), implementation);
    var classDef = new ImmutableClassDef(
        "Lsample/Target;", 0, "Ljava/lang/Object;", List.of(), null, Set.of(), List.of(), List.of(method));
    return new ImmutableDexFile(Opcodes.getDefault(), Set.of(classDef));
  }
}

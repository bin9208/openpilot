package ai.comma.naver.dexpatch;

import static org.junit.jupiter.api.Assertions.assertThrows;

import com.android.tools.smali.dexlib2.Opcode;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.iface.DexFile;
import com.android.tools.smali.dexlib2.immutable.ImmutableClassDef;
import com.android.tools.smali.dexlib2.immutable.ImmutableDexFile;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethod;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodImplementation;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodParameter;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction10x;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction11x;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction35c;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction3rc;
import com.android.tools.smali.dexlib2.immutable.reference.ImmutableMethodReference;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

final class ProductionFinalDexVerifierTest {
  private static final ImmutableMethodReference HOOK = new ImmutableMethodReference(
      "Lai/comma/naver/payload/ProductionHooks;", "onStatus",
      List.of("Ljava/lang/Object;"), "V");
  private static final PatchRule RULE = new PatchRule(
      new AnchorRule(
          "status", "classes11.dex", "Lsample/Target;",
          "emit(Ljava/lang/Object;)V", List.of("nop", "move-result-object")),
      HOOK, HookPlacement.afterInstruction(0), HookArgument.parameter(0));

  @Test
  void acceptsFinalDex_whenProfiledHookIsExactAndReachable() {
    DexFile source = dex(List.of(
        new ImmutableInstruction10x(Opcode.NOP),
        new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
        new ImmutableInstruction10x(Opcode.RETURN_VOID)));
    DexFile patched = DexPatchEngine.patchExact(
        List.of(new DexUnit("classes11.dex", source)), List.of(RULE)).get("classes11.dex");

    DexPatchMain.verifyExactFinalHooks(
        List.of(new DexUnit("classes11.dex", patched)), List.of(RULE));
  }

  @Test
  void rejectsFinalDex_whenHookMovedAcrossProfiledBoundary() {
    DexFile moved = dex(List.of(
        new ImmutableInstruction10x(Opcode.NOP),
        new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
        new ImmutableInstruction3rc(Opcode.INVOKE_STATIC_RANGE, 1, 1, HOOK),
        new ImmutableInstruction10x(Opcode.RETURN_VOID)));

    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyExactFinalHooks(List.of(new DexUnit("classes11.dex", moved)), List.of(RULE)));
  }

  @Test
  void rejectsFinalDex_whenHookUsesWrongRegister() {
    DexFile wrongRegister = dex(List.of(
        new ImmutableInstruction10x(Opcode.NOP),
        new ImmutableInstruction3rc(Opcode.INVOKE_STATIC_RANGE, 0, 1, HOOK),
        new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
        new ImmutableInstruction10x(Opcode.RETURN_VOID)));

    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyExactFinalHooks(
            List.of(new DexUnit("classes11.dex", wrongRegister)), List.of(RULE)));
  }

  @Test
  void rejectsFinalDex_whenHookUsesNonRangeOpcode() {
    DexFile wrongOpcode = dex(List.of(
        new ImmutableInstruction10x(Opcode.NOP),
        new ImmutableInstruction35c(Opcode.INVOKE_STATIC, 1, 1, 0, 0, 0, 0, HOOK),
        new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
        new ImmutableInstruction10x(Opcode.RETURN_VOID)));

    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyExactFinalHooks(
            List.of(new DexUnit("classes11.dex", wrongOpcode)), List.of(RULE)));
  }

  @Test
  void rejectsFinalDex_whenExactlyPlacedHookIsUnreachable() {
    DexFile unreachable = dex(List.of(
        new ImmutableInstruction10x(Opcode.RETURN_VOID),
        new ImmutableInstruction10x(Opcode.NOP),
        new ImmutableInstruction3rc(Opcode.INVOKE_STATIC_RANGE, 1, 1, HOOK),
        new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
        new ImmutableInstruction10x(Opcode.RETURN_VOID)));

    assertThrows(IllegalArgumentException.class, () ->
        DexPatchMain.verifyExactFinalHooks(
            List.of(new DexUnit("classes11.dex", unreachable)), List.of(RULE)));
  }

  private static DexFile dex(List<? extends com.android.tools.smali.dexlib2.iface.instruction.Instruction> instructions) {
    ImmutableMethod method = new ImmutableMethod(
        "Lsample/Target;", "emit",
        List.of(new ImmutableMethodParameter("Ljava/lang/Object;", Set.of(), null)),
        "V", 0x8, Set.of(), Set.of(),
        new ImmutableMethodImplementation(2, instructions, List.of(), List.of()));
    return new ImmutableDexFile(Opcodes.getDefault(), Set.of(new ImmutableClassDef(
        "Lsample/Target;", 0, "Ljava/lang/Object;", List.of(), null,
        Set.of(), List.of(), List.of(method))));
  }
}

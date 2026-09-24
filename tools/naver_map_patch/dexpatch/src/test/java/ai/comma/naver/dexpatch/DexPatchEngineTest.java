package ai.comma.naver.dexpatch;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertIterableEquals;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.Opcode;
import com.android.tools.smali.dexlib2.iface.DexFile;
import com.android.tools.smali.dexlib2.iface.MethodImplementation;
import com.android.tools.smali.dexlib2.iface.instruction.Instruction;
import com.android.tools.smali.dexlib2.iface.instruction.RegisterRangeInstruction;
import com.android.tools.smali.dexlib2.immutable.ImmutableClassDef;
import com.android.tools.smali.dexlib2.immutable.ImmutableDexFile;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethod;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodParameter;
import com.android.tools.smali.dexlib2.immutable.ImmutableExceptionHandler;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodImplementation;
import com.android.tools.smali.dexlib2.immutable.ImmutableTryBlock;
import com.android.tools.smali.dexlib2.immutable.debug.ImmutableLineNumber;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction10x;
import com.android.tools.smali.dexlib2.immutable.reference.ImmutableMethodReference;
import org.junit.jupiter.api.Test;

final class DexPatchEngineTest {
  @Test
  void insertsHook_whenMethodHasProtectedDebuggedBody() {
    // Given: a method body with a register, a try/catch range, and debug line metadata.
    MethodImplementation original = new ImmutableMethodImplementation(
        1,
        List.of(new ImmutableInstruction10x(Opcode.NOP), new ImmutableInstruction10x(Opcode.RETURN_VOID)),
        List.of(new ImmutableTryBlock(0, 1, List.of(new ImmutableExceptionHandler("Ljava/lang/Exception;", 1)))),
        List.of(new ImmutableLineNumber(0, 41)));
    ImmutableMethodReference hook = new ImmutableMethodReference("Lprobe/Hook;", "capture", List.of(), "V");

    // When: the deterministic engine inserts the static hook.
    MethodImplementation patched = DexPatchEngine.insertStaticCall(original, hook);

    // Then: only the hook is added; register, try/catch, debug, and original instruction structures survive.
    List<Instruction> instructions = toList(patched.getInstructions());
    assertEquals(1, patched.getRegisterCount());
    assertEquals(3, instructions.size());
    assertEquals(Opcode.INVOKE_STATIC, instructions.get(0).getOpcode());
    assertIterableEquals(
        List.of(Opcode.NOP, Opcode.RETURN_VOID),
        instructions.subList(1, instructions.size()).stream().map(Instruction::getOpcode).toList());
    assertEquals(1, List.copyOf(patched.getTryBlocks()).size());
    assertEquals(1, toList(patched.getDebugItems()).size());
    var tryBlock = List.copyOf(patched.getTryBlocks()).get(0);
    assertEquals(3, tryBlock.getStartCodeAddress());
    assertEquals(1, tryBlock.getCodeUnitCount());
    assertEquals(4, tryBlock.getExceptionHandlers().get(0).getHandlerCodeAddress());
    assertEquals(3, toList(patched.getDebugItems()).get(0).getCodeAddress());
  }

  @Test
  void emitsSameInstructionSequence_whenPatchingSameBodyTwice() {
    // Given: one immutable input implementation and one exact hook reference.
    MethodImplementation original = new ImmutableMethodImplementation(
        0, List.of(new ImmutableInstruction10x(Opcode.RETURN_VOID)), List.of(), List.of());
    ImmutableMethodReference hook = new ImmutableMethodReference("Lprobe/Hook;", "capture", List.of(), "V");

    // When: independent patch passes use the same inputs.
    MethodImplementation first = DexPatchEngine.insertStaticCall(original, hook);
    MethodImplementation second = DexPatchEngine.insertStaticCall(original, hook);

    // Then: their opcode sequences are bytecode-order deterministic.
    assertIterableEquals(
        toList(first.getInstructions()).stream().map(Instruction::getOpcode).toList(),
        toList(second.getInstructions()).stream().map(Instruction::getOpcode).toList());
  }

  @Test
  void usesWideExplicitParametersWhenResolvingProfiledArgumentRegister() {
    ImmutableMethod method = new ImmutableMethod(
        "Lsample/Wide;", "lane", List.of(
            new ImmutableMethodParameter("I", Set.of(), null),
            new ImmutableMethodParameter("J", Set.of(), null),
            new ImmutableMethodParameter("Ljava/lang/Object;", Set.of(), null)),
        "V", 0x8, Set.of(), Set.of(), new ImmutableMethodImplementation(
            5, List.of(new ImmutableInstruction10x(Opcode.NOP), new ImmutableInstruction10x(Opcode.RETURN_VOID)),
            List.of(), List.of()));
    DexFile source = new ImmutableDexFile(Opcodes.getDefault(), Set.of(new ImmutableClassDef(
        "Lsample/Wide;", 0, "Ljava/lang/Object;", List.of(), null, Set.of(), List.of(), List.of(method))));
    PatchRule rule = new PatchRule(
        new AnchorRule("wide_lane", "classes.dex", "Lsample/Wide;", "lane(IJLjava/lang/Object;)V", List.of("nop")),
        new ImmutableMethodReference("Lsample/Hook;", "capture", List.of("Ljava/lang/Object;"), "V"),
        HookPlacement.methodEntry(), HookArgument.parameter(2));

    Map<String, DexFile> patched = DexPatchEngine.patchExact(List.of(new DexUnit("classes.dex", source)), List.of(rule));
    Instruction hook = toList(patched.get("classes.dex").getClasses().iterator().next().getMethods()
        .iterator().next().getImplementation().getInstructions()).get(0);

    assertEquals(Opcode.INVOKE_STATIC_RANGE, hook.getOpcode());
    assertEquals(4, ((RegisterRangeInstruction) hook).getStartRegister());
  }

  private static <T> List<T> toList(Iterable<? extends T> values) {
    List<T> result = new ArrayList<>();
    values.forEach(result::add);
    return List.copyOf(result);
  }
}

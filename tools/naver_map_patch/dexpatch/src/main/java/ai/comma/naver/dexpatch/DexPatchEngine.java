package ai.comma.naver.dexpatch;

import com.android.tools.smali.dexlib2.Opcode;
import com.android.tools.smali.dexlib2.builder.MutableMethodImplementation;
import com.android.tools.smali.dexlib2.builder.instruction.BuilderInstruction35c;
import com.android.tools.smali.dexlib2.builder.instruction.BuilderInstruction3rc;
import com.android.tools.smali.dexlib2.iface.ClassDef;
import com.android.tools.smali.dexlib2.iface.DexFile;
import com.android.tools.smali.dexlib2.iface.Method;
import com.android.tools.smali.dexlib2.iface.MethodImplementation;
import com.android.tools.smali.dexlib2.iface.instruction.Instruction;
import com.android.tools.smali.dexlib2.iface.instruction.OneRegisterInstruction;
import com.android.tools.smali.dexlib2.iface.reference.MethodReference;
import com.android.tools.smali.dexlib2.immutable.ImmutableClassDef;
import com.android.tools.smali.dexlib2.immutable.ImmutableDexFile;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethod;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodImplementation;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class DexPatchEngine {
  private DexPatchEngine() {}

  public static MethodImplementation insertStaticCall(
      MethodImplementation implementation, MethodReference hook) {
    MutableMethodImplementation patched = new MutableMethodImplementation(implementation);
    patched.addInstruction(
        0,
        new BuilderInstruction35c(Opcode.INVOKE_STATIC, 0, 0, 0, 0, 0, 0, hook));
    return ImmutableMethodImplementation.of(patched);
  }

  public static MethodImplementation insertProfiledCall(
      MethodImplementation implementation, PatchRule rule, int matchStart, int parameterRegister) {
    List<Instruction> instructions = toList(implementation.getInstructions());
    int register = switch (rule.argument().kind()) {
      case PARAMETER -> parameterRegister;
      case WINDOW_REGISTER -> {
        Instruction source = instructions.get(matchStart + rule.argument().index());
        if (!(source instanceof OneRegisterInstruction oneRegister)) {
          throw new IllegalArgumentException("profiled hook source is not a one-register instruction");
        }
        yield oneRegister.getRegisterA();
      }
    };
    int insertionIndex = switch (rule.placement().kind()) {
      case METHOD_ENTRY -> 0;
      case BEFORE_INSTRUCTION -> matchStart + rule.placement().instructionIndex();
      case AFTER_INSTRUCTION -> matchStart + rule.placement().instructionIndex() + 1;
    };
    MutableMethodImplementation patched = new MutableMethodImplementation(implementation);
    patched.addInstruction(
        insertionIndex,
        new BuilderInstruction3rc(Opcode.INVOKE_STATIC_RANGE, register, 1, rule.hook()));
    return ImmutableMethodImplementation.of(patched);
  }

  public static Map<String, DexFile> patchExact(List<DexUnit> dexUnits, List<PatchRule> rules) {
    List<AnchorRule> anchors = rules.stream().map(PatchRule::anchor).toList();
    Map<String, Integer> counts = DexAnchorScanner.scan(dexUnits, anchors);
    for (PatchRule rule : rules) {
      if (counts.getOrDefault(rule.anchor().name(), 0) != 1) {
        throw new IllegalArgumentException(
            "anchor exact-one mismatch: " + rule.anchor().name() + "="
                + counts.getOrDefault(rule.anchor().name(), 0));
      }
    }
    Map<String, DexFile> patched = new LinkedHashMap<>();
    for (DexUnit unit : dexUnits) {
      List<PatchRule> unitRules = rules.stream()
          .filter(rule -> rule.anchor().dexEntry().equals(unit.entryName()))
          .toList();
      if (!unitRules.isEmpty()) {
        patched.put(unit.entryName(), patchDex(unit.dexFile(), unitRules));
      }
    }
    return Map.copyOf(patched);
  }

  private static DexFile patchDex(DexFile dexFile, List<PatchRule> rules) {
    List<ClassDef> classes = new ArrayList<>();
    for (ClassDef classDef : dexFile.getClasses()) {
      List<PatchRule> classRules = rules.stream()
          .filter(rule -> rule.anchor().classDescriptor().equals(classDef.getType()))
          .toList();
      if (classRules.isEmpty()) {
        classes.add(classDef);
        continue;
      }
      List<Method> methods = new ArrayList<>();
      for (Method method : classDef.getMethods()) {
        List<PatchRule> methodRules = classRules.stream()
            .filter(rule -> rule.anchor().methodDescriptor().equals(DexAnchorScanner.shortDescriptor(method)))
            .toList();
        methods.add(methodRules.isEmpty() ? method : patchedMethod(method, methodRules));
      }
      classes.add(new ImmutableClassDef(
          classDef.getType(), classDef.getAccessFlags(), classDef.getSuperclass(), classDef.getInterfaces(),
          classDef.getSourceFile(), classDef.getAnnotations(), classDef.getFields(), methods));
    }
    return new ImmutableDexFile(dexFile.getOpcodes(), classes);
  }

  private static Method patchedMethod(Method method, List<PatchRule> rules) {
    MethodImplementation original = method.getImplementation();
    if (original == null) {
      throw new IllegalArgumentException("profiled hook target has no implementation");
    }
    List<ResolvedHook> hooks = new ArrayList<>();
    for (PatchRule rule : rules) {
      int matchStart = DexAnchorScanner.findWindows(original, rule.anchor().instructionWindow()).get(0);
      int insertionIndex = switch (rule.placement().kind()) {
        case METHOD_ENTRY -> 0;
        case BEFORE_INSTRUCTION -> matchStart + rule.placement().instructionIndex();
        case AFTER_INSTRUCTION -> matchStart + rule.placement().instructionIndex() + 1;
      };
      int parameterRegister = rule.argument().kind() == HookArgument.Kind.PARAMETER
          ? explicitParameterRegister(method, rule.argument().index()) : 0;
      hooks.add(new ResolvedHook(rule, matchStart, parameterRegister, insertionIndex));
    }
    hooks.sort(Comparator.comparingInt(ResolvedHook::insertionIndex).reversed());
    MethodImplementation implementation = original;
    for (ResolvedHook hook : hooks) {
      implementation = insertProfiledCall(
          implementation, hook.rule(), hook.matchStart(), hook.parameterRegister());
    }
    return new ImmutableMethod(
        method.getDefiningClass(), method.getName(), method.getParameters(), method.getReturnType(),
        method.getAccessFlags(), method.getAnnotations(), method.getHiddenApiRestrictions(), implementation);
  }

  private static int explicitParameterRegister(Method method, int selectedIndex) {
    List<? extends CharSequence> types = method.getParameterTypes();
    if (selectedIndex >= types.size()) {
      throw new IllegalArgumentException("profiled hook parameter index is out of range");
    }
    MethodImplementation implementation = method.getImplementation();
    int parameterWidth = (method.getAccessFlags() & 0x8) == 0 ? 1 : 0;
    for (CharSequence type : types) {
      parameterWidth += isWide(type) ? 2 : 1;
    }
    int register = implementation.getRegisterCount() - parameterWidth;
    if ((method.getAccessFlags() & 0x8) == 0) {
      register++;
    }
    for (int index = 0; index < selectedIndex; index++) {
      register += isWide(types.get(index)) ? 2 : 1;
    }
    return register;
  }

  private static boolean isWide(CharSequence type) {
    return type.length() == 1 && (type.charAt(0) == 'J' || type.charAt(0) == 'D');
  }

  private static <T> List<T> toList(Iterable<? extends T> values) {
    List<T> result = new ArrayList<>();
    values.forEach(result::add);
    return List.copyOf(result);
  }

  private record ResolvedHook(
      PatchRule rule, int matchStart, int parameterRegister, int insertionIndex) {}
}

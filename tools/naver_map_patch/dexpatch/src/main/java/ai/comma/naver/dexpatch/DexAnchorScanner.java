package ai.comma.naver.dexpatch;

import com.android.tools.smali.dexlib2.iface.ClassDef;
import com.android.tools.smali.dexlib2.iface.Method;
import com.android.tools.smali.dexlib2.iface.MethodImplementation;
import com.android.tools.smali.dexlib2.iface.instruction.Instruction;
import com.android.tools.smali.dexlib2.iface.instruction.ReferenceInstruction;
import com.android.tools.smali.dexlib2.util.ReferenceUtil;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class DexAnchorScanner {
  private DexAnchorScanner() {}

  public static Map<String, Integer> scan(List<DexUnit> dexUnits, List<AnchorRule> rules) {
    Map<String, Integer> result = new LinkedHashMap<>();
    for (AnchorRule rule : rules) {
      int matches = 0;
      for (DexUnit unit : dexUnits) {
        if (!unit.entryName().equals(rule.dexEntry())) {
          continue;
        }
        for (ClassDef classDef : unit.dexFile().getClasses()) {
          if (!classDef.getType().equals(rule.classDescriptor())) {
            continue;
          }
          for (Method method : classDef.getMethods()) {
            if (!shortDescriptor(method).equals(rule.methodDescriptor())) {
              continue;
            }
            matches += countWindows(method.getImplementation(), rule.instructionWindow());
          }
        }
      }
      result.put(rule.name(), matches);
    }
    return Map.copyOf(result);
  }

  static List<String> normalize(MethodImplementation implementation) {
    List<String> result = new ArrayList<>();
    for (Instruction instruction : implementation.getInstructions()) {
      String token = instruction.getOpcode().name;
      if (instruction instanceof ReferenceInstruction referenced) {
        token += " " + ReferenceUtil.getReferenceString(referenced.getReference());
      }
      result.add(token);
    }
    return List.copyOf(result);
  }

  private static int countWindows(MethodImplementation implementation, List<String> window) {
    if (implementation == null) {
      return 0;
    }
    return findWindows(implementation, window).size();
  }

  static List<Integer> findWindows(MethodImplementation implementation, List<String> window) {
    if (implementation == null) {
      return List.of();
    }
    List<String> instructions = normalize(implementation);
    List<Integer> matches = new ArrayList<>();
    for (int start = 0; start + window.size() <= instructions.size(); start++) {
      if (instructions.subList(start, start + window.size()).equals(window)) {
        matches.add(start);
      }
    }
    return List.copyOf(matches);
  }

  static String shortDescriptor(Method method) {
    StringBuilder result = new StringBuilder(method.getName()).append('(');
    for (CharSequence parameter : method.getParameterTypes()) {
      result.append(parameter);
    }
    return result.append(')').append(method.getReturnType()).toString();
  }
}

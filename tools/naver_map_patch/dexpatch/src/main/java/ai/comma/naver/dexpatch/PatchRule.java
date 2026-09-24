package ai.comma.naver.dexpatch;

import com.android.tools.smali.dexlib2.iface.reference.MethodReference;

public record PatchRule(
    AnchorRule anchor,
    MethodReference hook,
    HookPlacement placement,
    HookArgument argument) {
  public PatchRule {
    if (!hook.getParameterTypes().equals(java.util.List.of("Ljava/lang/Object;"))
        || !hook.getReturnType().equals("V")) {
      throw new IllegalArgumentException("diagnostic hooks must accept one Object and return void");
    }
    if (placement.kind() != HookPlacement.Kind.METHOD_ENTRY
        && placement.instructionIndex() >= anchor.instructionWindow().size()) {
      throw new IllegalArgumentException("hook placement must point inside the exact anchor window");
    }
    if (argument.kind() == HookArgument.Kind.WINDOW_REGISTER
        && argument.index() >= anchor.instructionWindow().size()) {
      throw new IllegalArgumentException("hook register source must point inside the exact anchor window");
    }
  }
}

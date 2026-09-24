package ai.comma.naver.dexpatch;

import java.util.List;

public record AnchorRule(
    String name,
    String dexEntry,
    String classDescriptor,
    String methodDescriptor,
    List<String> instructionWindow) {
  public AnchorRule {
    if (name.isBlank()
        || dexEntry.isBlank()
        || classDescriptor.isBlank()
        || methodDescriptor.isBlank()
        || instructionWindow.isEmpty()
        || instructionWindow.stream().anyMatch(String::isBlank)) {
      throw new IllegalArgumentException("anchor fields must be non-empty");
    }
    instructionWindow = List.copyOf(instructionWindow);
  }
}

package ai.comma.naver.dexpatch;

import com.android.tools.smali.dexlib2.iface.DexFile;

public record DexUnit(String entryName, DexFile dexFile) {
  public DexUnit {
    if (entryName.isBlank()) {
      throw new IllegalArgumentException("DEX entry name must be non-empty");
    }
  }
}

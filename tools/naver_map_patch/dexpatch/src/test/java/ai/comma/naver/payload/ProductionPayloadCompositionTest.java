package ai.comma.naver.payload;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.android.tools.smali.dexlib2.DexFileFactory;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.dexbacked.DexBackedDexFile;
import com.android.tools.smali.dexlib2.iface.ClassDef;
import com.android.tools.smali.dexlib2.iface.Method;
import com.android.tools.smali.dexlib2.iface.instruction.Instruction;
import com.android.tools.smali.dexlib2.iface.instruction.ReferenceInstruction;
import java.io.File;
import java.util.HashSet;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

final class ProductionPayloadCompositionTest {
  @Test
  void productionDexContainsOnlyTheReviewedProductionClasses() throws Exception {
    Set<String> types = productionClasses().stream()
        .map(type -> type.indexOf('$') >= 0 ? type.substring(0, type.indexOf('$')) + ";" : type)
        .collect(Collectors.toSet());

    assertEquals(Set.of(
        "Lai/comma/naver/payload/Naver6805ObjectMapper;",
        "Lai/comma/naver/payload/NaverNavigationAggregator;",
        "Lai/comma/naver/payload/NaverNavigationEnvelope;",
        "Lai/comma/naver/payload/NaverNavigationSender;",
        "Lai/comma/naver/payload/NaverNavigationState;",
        "Lai/comma/naver/payload/ProductionHooks;",
        "Lai/comma/naver/payload/ProductionRuntime;"), types);
  }

  @Test
  void productionDexHasNoSimulationDiagnosticLaneOrSyntheticRouteInventory() throws Exception {
    String inventory = String.join("\n", productionReferences()).toLowerCase();

    for (String forbidden : Set.of(
        "navigationsimulation", "simulation_enabled", "simulation", "diagnosticruntime",
        "diagnosticsampler", "offlinecapturestore", "onlane", "navilaneitem",
        "synthetic maneuver", "synthetic route")) {
      assertFalse(inventory.contains(forbidden), "forbidden production DEX token: " + forbidden);
    }
    assertTrue(inventory.contains("onroute"));
    assertTrue(inventory.contains("currentroute"));
  }

  @Test
  void productionDexContainsBroadcastDiscoveryAndNoLoopbackTransportLiteral() throws Exception {
    String inventory = String.join("\n", productionReferences());

    assertTrue(inventory.contains(
        "{\"type\":\"carrot.navigation.discover\",\"source\":\"naver\",\"schema_version\":1}"));
    assertTrue(inventory.contains("carrot.navigation.discover.response"));
    assertTrue(inventory.contains("255.255.255.255"));
    assertFalse(inventory.contains("127.0.0.1"));
  }

  private static Set<String> productionClasses() throws Exception {
    String path = System.getProperty("naver.production.payloadDex");
    return DexFileFactory.loadDexFile(new File(path), Opcodes.getDefault()).getClasses().stream()
        .map(ClassDef::getType).collect(Collectors.toSet());
  }

  private static Set<String> productionReferences() throws Exception {
    String path = System.getProperty("naver.production.payloadDex");
    Set<String> references = new HashSet<>();
    var dexFile = DexFileFactory.loadDexFile(new File(path), Opcodes.getDefault());
    if (dexFile instanceof DexBackedDexFile backed) {
      backed.getStringReferences().forEach(reference -> references.add(reference.getString()));
    }
    for (ClassDef classDef : dexFile.getClasses()) {
      references.add(classDef.getType());
      classDef.getFields().forEach(field -> {
        references.add(field.getName());
        references.add(field.getType());
        if (field.getInitialValue() != null) references.add(field.getInitialValue().toString());
      });
      for (Method method : classDef.getMethods()) {
        if (method.getImplementation() == null) continue;
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

package ai.comma.naver.dexpatch;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.android.tools.smali.dexlib2.Opcode;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.iface.DexFile;
import com.android.tools.smali.dexlib2.iface.Method;
import com.android.tools.smali.dexlib2.iface.instruction.Instruction;
import com.android.tools.smali.dexlib2.iface.instruction.ReferenceInstruction;
import com.android.tools.smali.dexlib2.iface.instruction.RegisterRangeInstruction;
import com.android.tools.smali.dexlib2.immutable.ImmutableClassDef;
import com.android.tools.smali.dexlib2.immutable.ImmutableDexFile;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethod;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodImplementation;
import com.android.tools.smali.dexlib2.immutable.ImmutableMethodParameter;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction10x;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction12x;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction11x;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction21c;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction22c;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction35c;
import com.android.tools.smali.dexlib2.immutable.instruction.ImmutableInstruction3rc;
import com.android.tools.smali.dexlib2.immutable.reference.ImmutableFieldReference;
import com.android.tools.smali.dexlib2.immutable.reference.ImmutableMethodReference;
import com.android.tools.smali.dexlib2.immutable.reference.ImmutableTypeReference;
import com.android.tools.smali.dexlib2.util.ReferenceUtil;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;

/** Runs the real, Gradle-generated profile rules against a matching synthetic DEX fixture. */
final class DexDiagnosticPatchIntegrationTest {
  private static final String HOOKS = "Lai/comma/naver/payload/DiagnosticHooks;";
  private static final String STATUS = "Lcom/naver/map/core/navigation/NaviStatusBroadcaster;";
  private static final String SAFETY_STORE = "Lcom/naver/map/core/navigation/SafeControlItemStore;";
  private static final String SAFETY_MANAGER =
      "Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;";
  private static final String NAVI_STORE = "Lcom/naver/map/core/navigation/NaviStore;";
  private static final String TBT_DATA = "Lcom/naver/map/core/navigation/model/TbtData;";
  private static final String TBT_ITEM = "Lcom/naver/map/core/navigation/model/TbtItem;";
  private static final String FILE_PROVIDER = "Landroidx/core/content/FileProvider;";

  @Test
  void actualGeneratedProfileRulesPatchSevenNavigationAndOneInfrastructureHook() {
    List<PatchRule> navigationRules = actualNavigationRules();
    PatchRule infrastructureRule = actualInfrastructureRule();
    List<PatchRule> rules = new ArrayList<>(navigationRules);
    rules.add(infrastructureRule);
    List<DexUnit> source = sourceUnits(false);

    assertEquals("6.8.0.5", DiagnosticProfileContract.PROFILE_ID);
    assertEquals(
        Map.of("status", 1, "tbt_current", 1, "tbt_next", 1,
            "safety_source", 1, "safety", 1, "route", 1, "lane", 1),
        DexAnchorScanner.scan(
            source, navigationRules.stream().map(PatchRule::anchor).toList()));
    assertEquals(
        Map.of("capture_export_quiesce", 1),
        DexAnchorScanner.scan(source, List.of(infrastructureRule.anchor())));

    Map<String, DexFile> patched = DexPatchEngine.patchExact(source, rules);

    assertHook(patched.get("classes11.dex"), STATUS, "g", "onStatus", 0, 1);
    assertHook(patched.get("classes11.dex"), TBT_DATA, "<init>", "onCurrentTbt", 2, 2);
    assertHook(patched.get("classes11.dex"), TBT_DATA, "<init>", "onNextTbt", 4, 3);
    assertHook(patched.get("classes11.dex"), SAFETY_MANAGER, "l", "onSafetySource", 0, 3);
    assertHook(patched.get("classes11.dex"), SAFETY_STORE, "k", "onSafety", 2, 0);
    assertHook(patched.get("classes11.dex"), NAVI_STORE, "q1", "onRoute", 0, 1);
    assertHook(patched.get("classes11.dex"), NAVI_STORE, "M0", "onLane", 20, 2);
    assertHook(
        patched.get("classes.dex"), FILE_PROVIDER, "openFile",
        "beforeCaptureRead", 0, 2);
    assertEquals(
        Opcode.INVOKE_VIRTUAL,
        instructions(method(
            patched.get("classes.dex"), FILE_PROVIDER, "openFile")
            .getImplementation().getInstructions()).get(1).getOpcode());
  }

  @Test
  void actualProfileDuplicateAnchorFailsExactOneWithoutMutatingSource() {
    List<DexUnit> source = sourceUnits(true);

    assertThrows(
        IllegalArgumentException.class,
        () -> DexPatchEngine.patchExact(source, actualNavigationRules()));
    assertEquals(Opcode.INVOKE_INTERFACE, instructions(method(
        source.get(1).dexFile(), STATUS, "g").getImplementation().getInstructions()).get(0).getOpcode());
  }

  private static List<PatchRule> actualNavigationRules() {
    return Arrays.stream(DiagnosticProfileContract.encodedNavigationPatchRules())
        .map(DexPatchMain::decodePatchRule).toList();
  }

  private static PatchRule actualInfrastructureRule() {
    return DexPatchMain.decodePatchRule(
        DiagnosticProfileContract.encodedInfrastructurePatchRule());
  }

  private static void assertHook(
      DexFile dexFile, String type, String methodName, String hookName, int instructionIndex, int register) {
    Instruction instruction = instructions(method(dexFile, type, methodName).getImplementation()
        .getInstructions()).get(instructionIndex);
    assertEquals(Opcode.INVOKE_STATIC_RANGE, instruction.getOpcode());
    assertEquals(register, ((RegisterRangeInstruction) instruction).getStartRegister());
    assertEquals(HOOKS + "->" + hookName + "(Ljava/lang/Object;)V",
        ReferenceUtil.getReferenceString(((ReferenceInstruction) instruction).getReference()));
  }

  private static List<DexUnit> sourceUnits(boolean duplicateStatus) {
    return List.of(
        new DexUnit("classes4.dex", new ImmutableDexFile(Opcodes.getDefault(), Set.of())),
        new DexUnit("classes11.dex", new ImmutableDexFile(Opcodes.getDefault(), Set.of(
            new ImmutableClassDef(STATUS, 0, "Ljava/lang/Object;", List.of(), null, Set.of(), List.of(),
                List.of(statusMethod(duplicateStatus))),
            new ImmutableClassDef(SAFETY_STORE, 0, "Ljava/lang/Object;", List.of(), null, Set.of(), List.of(),
                List.of(safetyMethod())),
            new ImmutableClassDef(SAFETY_MANAGER, 0, "Ljava/lang/Object;", List.of(), null, Set.of(), List.of(),
                List.of(safetySourceMethod())),
            new ImmutableClassDef(TBT_DATA, 0, "Ljava/lang/Object;", List.of(), null, Set.of(), List.of(),
                List.of(tbtConstructor())),
            new ImmutableClassDef(NAVI_STORE, 0, "Ljava/lang/Object;", List.of(), null, Set.of(), List.of(),
                List.of(routeMethod(), laneMethod()))))),
        new DexUnit("classes.dex", new ImmutableDexFile(Opcodes.getDefault(), Set.of(
            new ImmutableClassDef(
                FILE_PROVIDER, 1, "Ljava/lang/Object;", List.of(), null,
                Set.of(), List.of(), List.of(fileProviderMethod()))))));
  }

  private static Method statusMethod(boolean duplicate) {
    List<Instruction> values = new ArrayList<>(statusWindow());
    if (duplicate) values.addAll(statusWindow());
    values.add(new ImmutableInstruction10x(Opcode.RETURN_VOID));
    return method(STATUS, "g", List.of("Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;"), "V", 0, 2, values);
  }

  private static List<Instruction> statusWindow() {
    return List.of(
        invoke(Opcode.INVOKE_INTERFACE, "Lcom/naver/map/core/navigation/NaviStatusBroadcaster$HasGoal;", "getCoord", List.of(), "Lcom/naver/maps/geometry/LatLng;"),
        new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
        new ImmutableInstruction22c(Opcode.IGET_WIDE, 0, 0,
            new ImmutableFieldReference("Lcom/naver/maps/geometry/LatLng;", "latitude", "D")));
  }

  private static Method tbtConstructor() {
    return method(TBT_DATA, "<init>", List.of(TBT_ITEM, TBT_ITEM), "V", 0, 4, List.of(
        invoke(Opcode.INVOKE_STATIC, "Lkotlin/jvm/internal/Intrinsics;", "checkNotNullParameter",
            List.of("Ljava/lang/Object;", "Ljava/lang/String;"), "V"),
        invoke(Opcode.INVOKE_DIRECT, "Ljava/lang/Object;", "<init>", List.of(), "V"),
        new ImmutableInstruction22c(
            Opcode.IPUT_OBJECT, 2, 1, new ImmutableFieldReference(TBT_DATA, "a", TBT_ITEM)),
        new ImmutableInstruction22c(
            Opcode.IPUT_OBJECT, 3, 1, new ImmutableFieldReference(TBT_DATA, "b", TBT_ITEM)),
        new ImmutableInstruction10x(Opcode.RETURN_VOID)));
  }

  private static Method safetyMethod() {
    return method(SAFETY_STORE, "k", List.of(), "Lcom/naver/map/core/common/model/SafeControlItem;", 0, 1, List.of(
        new ImmutableInstruction3rc(Opcode.INVOKE_VIRTUAL_RANGE, 0, 0,
            new ImmutableMethodReference("Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;", "m",
                List.of("Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;", "Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;", "Z", "Z", "Z"), "Lcom/naver/map/core/common/model/SafeControlItem;")),
        new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
        new ImmutableInstruction11x(Opcode.RETURN_OBJECT, 0)));
  }

  private static Method safetySourceMethod() {
    String guidanceSafety =
        "Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;";
    String safetyCode =
        "Lcom/naver/maps/navi/v2/shared/api/route/constants/SafetyCode;";
    return method(
        SAFETY_MANAGER,
        "l",
        List.of(guidanceSafety),
        "Lcom/naver/map/core/common/model/SafetyExtra;",
        0,
        4,
        List.of(
            invoke(Opcode.INVOKE_INTERFACE, guidanceSafety, "getCode", List.of(), safetyCode),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
            invoke(Opcode.INVOKE_INTERFACE, guidanceSafety, "distance", List.of(), "D"),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_WIDE, 0),
            new ImmutableInstruction11x(Opcode.RETURN_OBJECT, 0)));
  }

  private static Method routeMethod() {
    return method(NAVI_STORE, "q1", List.of("Lcom/naver/map/core/navigation/model/CurrentRoute;"), "V", 0, 2, List.of(
        new ImmutableInstruction22c(Opcode.IGET_OBJECT, 0, 0,
            new ImmutableFieldReference(NAVI_STORE, "T0", "Lcom/naver/map/base/common/lifecycle/MutableLiveData;")),
        invoke(Opcode.INVOKE_VIRTUAL, "Lcom/naver/map/base/common/lifecycle/MutableLiveData;", "setValue", List.of("Ljava/lang/Object;"), "V"),
        new ImmutableInstruction10x(Opcode.RETURN_VOID)));
  }

  private static Method laneMethod() {
    String guidanceLane = "Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceLane;";
    String routeLane = "Lcom/naver/maps/navi/v2/shared/api/route/model/RouteLane;";
    String laneItem = "Lcom/naver/map/core/navigation/lane/NaviLaneItem;";
    return method(
        NAVI_STORE,
        "M0",
        List.of("Lcom/naver/maps/navi/v2/api/GuidanceSession;"),
        "V",
        0,
        14,
        List.of(
            new ImmutableInstruction21c(
                Opcode.NEW_INSTANCE, 2, new ImmutableTypeReference(laneItem)),
            invoke(Opcode.INVOKE_INTERFACE, guidanceLane, "getDistance-Y4BO_gI", List.of(), "D"),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_WIDE, 3),
            invoke(Opcode.INVOKE_INTERFACE, guidanceLane, "getInfo", List.of(), routeLane),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 5),
            invoke(Opcode.INVOKE_INTERFACE, routeLane, "getUnits", List.of(), "Ljava/util/List;"),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 5),
            invoke(
                Opcode.INVOKE_STATIC,
                "Lcom/naver/map/AppContext;",
                "c",
                List.of(),
                "Lcom/naver/map/core/common/util/AppType;"),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 6),
            invoke(
                Opcode.INVOKE_VIRTUAL,
                "Lcom/naver/map/core/common/util/AppType;",
                "i",
                List.of(),
                "Z"),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT, 6),
            new ImmutableInstruction12x(Opcode.XOR_INT_2ADDR, 6, 7),
            invoke(
                Opcode.INVOKE_STATIC,
                "Lcom/naver/map/core/navigation/lane/NaviLaneItemKt;",
                "f",
                List.of(
                    "Ljava/util/List;",
                    "Lcom/naver/maps/navi/v2/shared/api/route/model/RouteGuideImage;",
                    "Z"),
                "Ljava/util/List;"),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 5),
            invoke(Opcode.INVOKE_INTERFACE, guidanceLane, "getInfo", List.of(), routeLane),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 3),
            invoke(Opcode.INVOKE_INTERFACE, routeLane, "getUnits", List.of(), "Ljava/util/List;"),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 6),
            new ImmutableInstruction22c(
                Opcode.IGET_BOOLEAN, 7, 8, new ImmutableFieldReference(NAVI_STORE, "h1", "Z")),
            new ImmutableInstruction3rc(
                Opcode.INVOKE_DIRECT_RANGE,
                2,
                6,
                new ImmutableMethodReference(
                    laneItem,
                    "<init>",
                    List.of("D", "Ljava/util/List;", "Ljava/util/List;", "Z"),
                    "V")),
            new ImmutableInstruction10x(Opcode.RETURN_VOID)));
  }

  private static Method fileProviderMethod() {
    String strategy = "Landroidx/core/content/FileProvider$PathStrategy;";
    return method(
        FILE_PROVIDER,
        "openFile",
        List.of("Landroid/net/Uri;", "Ljava/lang/String;"),
        "Landroid/os/ParcelFileDescriptor;",
        1,
        4,
        List.of(
            new ImmutableInstruction35c(
                Opcode.INVOKE_VIRTUAL,
                1, 1, 0, 0, 0, 0,
                new ImmutableMethodReference(
                    FILE_PROVIDER, "f", List.of(), strategy)),
            new ImmutableInstruction11x(Opcode.MOVE_RESULT_OBJECT, 0),
            new ImmutableInstruction35c(
                Opcode.INVOKE_INTERFACE,
                2, 0, 2, 0, 0, 0,
                new ImmutableMethodReference(
                    strategy, "b", List.of("Landroid/net/Uri;"), "Ljava/io/File;")),
            new ImmutableInstruction11x(Opcode.RETURN_OBJECT, 0)));
  }

  private static Instruction invoke(Opcode opcode, String definingClass, String name, List<String> parameters, String returnType) {
    return new ImmutableInstruction35c(opcode, 0, 0, 0, 0, 0, 0,
        new ImmutableMethodReference(definingClass, name, parameters, returnType));
  }

  private static Method method(String type, String name, List<String> parameters, String returnType,
      int accessFlags, int registers, List<Instruction> instructions) {
    return new ImmutableMethod(type, name, parameters.stream()
        .map(parameter -> new ImmutableMethodParameter(parameter, Set.of(), null)).toList(), returnType,
        accessFlags, Set.of(), Set.of(), new ImmutableMethodImplementation(registers, instructions, List.of(), List.of()));
  }

  private static Method method(DexFile dexFile, String type, String name) {
    for (var classDef : dexFile.getClasses()) {
      if (!classDef.getType().equals(type)) continue;
      for (Method method : classDef.getMethods()) {
        if (method.getName().equals(name)) return method;
      }
    }
    throw new AssertionError("missing method " + type + "->" + name);
  }

  private static List<Instruction> instructions(Iterable<? extends Instruction> source) {
    List<Instruction> values = new ArrayList<>();
    source.forEach(values::add);
    return values;
  }
}

package ai.comma.naver.dexpatch;

import com.android.tools.smali.dexlib2.DexFileFactory;
import com.android.tools.smali.dexlib2.Opcode;
import com.android.tools.smali.dexlib2.Opcodes;
import com.android.tools.smali.dexlib2.dexbacked.DexBackedDexFile;
import com.android.tools.smali.dexlib2.iface.DexFile;
import com.android.tools.smali.dexlib2.iface.ClassDef;
import com.android.tools.smali.dexlib2.iface.Method;
import com.android.tools.smali.dexlib2.iface.MultiDexContainer;
import com.android.tools.smali.dexlib2.iface.instruction.Instruction;
import com.android.tools.smali.dexlib2.iface.instruction.OffsetInstruction;
import com.android.tools.smali.dexlib2.iface.instruction.OneRegisterInstruction;
import com.android.tools.smali.dexlib2.iface.instruction.ReferenceInstruction;
import com.android.tools.smali.dexlib2.iface.instruction.RegisterRangeInstruction;
import com.android.tools.smali.dexlib2.iface.instruction.SwitchPayload;
import com.android.tools.smali.dexlib2.iface.reference.MethodReference;
import com.android.tools.smali.dexlib2.iface.value.StringEncodedValue;
import com.android.tools.smali.dexlib2.immutable.reference.ImmutableMethodReference;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.ArrayDeque;
import java.util.Comparator;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

public final class DexPatchMain {
  private static final String FIELD_SEPARATOR = Character.toString(0x1f);
  private static final String PRODUCTION_BUILD_ID = "naver-6.8.0.5-public-beta-v2";
  private static final Set<String> PRODUCTION_TOP_LEVEL_TYPES = Set.of(
      "Lai/comma/naver/payload/Naver6805ObjectMapper;",
      "Lai/comma/naver/payload/NaverNavigationAggregator;",
      "Lai/comma/naver/payload/NaverNavigationEnvelope;",
      "Lai/comma/naver/payload/NaverNavigationSender;",
      "Lai/comma/naver/payload/NaverNavigationState;",
      "Lai/comma/naver/payload/ProductionHooks;",
      "Lai/comma/naver/payload/ProductionRuntime;");
  private static final Map<String, List<String>> PRODUCTION_BINDINGS = Map.of(
      "status", List.of(
          "Lcom/naver/map/core/navigation/NaviStatusBroadcaster;",
          "g(Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;)V",
          "onStatus(Ljava/lang/Object;)V"),
      "tbt_current", List.of(
          "Lcom/naver/map/core/navigation/model/TbtData;",
          "<init>(Lcom/naver/map/core/navigation/model/TbtItem;Lcom/naver/map/core/navigation/model/TbtItem;)V",
          "onCurrentTbt(Ljava/lang/Object;)V"),
      "tbt_next", List.of(
          "Lcom/naver/map/core/navigation/model/TbtData;",
          "<init>(Lcom/naver/map/core/navigation/model/TbtItem;Lcom/naver/map/core/navigation/model/TbtItem;)V",
          "onNextTbt(Ljava/lang/Object;)V"),
      "safety_source", List.of(
          "Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;",
          "l(Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;)Lcom/naver/map/core/common/model/SafetyExtra;",
          "onSafetySource(Ljava/lang/Object;)V"),
      "safety", List.of(
          "Lcom/naver/map/core/navigation/SafeControlItemStore;",
          "k()Lcom/naver/map/core/common/model/SafeControlItem;",
          "onSafety(Ljava/lang/Object;)V"),
      "route", List.of(
          "Lcom/naver/map/core/navigation/NaviStore;",
          "q1(Lcom/naver/map/core/navigation/model/CurrentRoute;)V",
          "onRoute(Ljava/lang/Object;)V"));
  private static final Map<String, PatchRule> PRODUCTION_RULES = Map.of(
      "status", productionRule(
          "status", "Lcom/naver/map/core/navigation/NaviStatusBroadcaster;",
          "g(Lcom/naver/map/core/navigation/NaviStatusBroadcaster$Status;)V",
          List.of(
              "invoke-interface Lcom/naver/map/core/navigation/NaviStatusBroadcaster$HasGoal;->getCoord()Lcom/naver/maps/geometry/LatLng;",
              "move-result-object",
              "iget-wide Lcom/naver/maps/geometry/LatLng;->latitude:D"),
          "onStatus", HookPlacement.methodEntry(), HookArgument.parameter(0)),
      "tbt_current", productionRule(
          "tbt_current", "Lcom/naver/map/core/navigation/model/TbtData;",
          "<init>(Lcom/naver/map/core/navigation/model/TbtItem;Lcom/naver/map/core/navigation/model/TbtItem;)V",
          List.of(
              "invoke-static Lkotlin/jvm/internal/Intrinsics;->checkNotNullParameter(Ljava/lang/Object;Ljava/lang/String;)V",
              "invoke-direct Ljava/lang/Object;-><init>()V",
              "iput-object Lcom/naver/map/core/navigation/model/TbtData;->a:Lcom/naver/map/core/navigation/model/TbtItem;"),
          "onCurrentTbt", HookPlacement.afterInstruction(1), HookArgument.parameter(0)),
      "tbt_next", productionRule(
          "tbt_next", "Lcom/naver/map/core/navigation/model/TbtData;",
          "<init>(Lcom/naver/map/core/navigation/model/TbtItem;Lcom/naver/map/core/navigation/model/TbtItem;)V",
          List.of(
              "invoke-direct Ljava/lang/Object;-><init>()V",
              "iput-object Lcom/naver/map/core/navigation/model/TbtData;->a:Lcom/naver/map/core/navigation/model/TbtItem;",
              "iput-object Lcom/naver/map/core/navigation/model/TbtData;->b:Lcom/naver/map/core/navigation/model/TbtItem;"),
          "onNextTbt", HookPlacement.beforeInstruction(2), HookArgument.parameter(1)),
      "safety_source", productionRule(
          "safety_source", "Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;",
          "l(Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;)Lcom/naver/map/core/common/model/SafetyExtra;",
          List.of(
              "invoke-interface Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;->getCode()Lcom/naver/maps/navi/v2/shared/api/route/constants/SafetyCode;",
              "move-result-object",
              "invoke-interface Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;->distance()D"),
          "onSafetySource", HookPlacement.methodEntry(), HookArgument.parameter(0)),
      "safety", productionRule(
          "safety", "Lcom/naver/map/core/navigation/SafeControlItemStore;",
          "k()Lcom/naver/map/core/common/model/SafeControlItem;",
          List.of(
              "invoke-virtual/range Lcom/naver/map/core/navigation/NaviSafetyControllItemManager;->m(Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;Lcom/naver/maps/navi/v2/api/guidance/model/GuidanceSafety;ZZZ)Lcom/naver/map/core/common/model/SafeControlItem;",
              "move-result-object",
              "return-object"),
          "onSafety", HookPlacement.beforeInstruction(2), HookArgument.windowRegister(2)),
      "route", productionRule(
          "route", "Lcom/naver/map/core/navigation/NaviStore;",
          "q1(Lcom/naver/map/core/navigation/model/CurrentRoute;)V",
          List.of(
              "iget-object Lcom/naver/map/core/navigation/NaviStore;->T0:Lcom/naver/map/base/common/lifecycle/MutableLiveData;",
              "invoke-virtual Lcom/naver/map/base/common/lifecycle/MutableLiveData;->setValue(Ljava/lang/Object;)V",
              "return-void"),
          "onRoute", HookPlacement.methodEntry(), HookArgument.parameter(0)));
  private static final Set<String> FORBIDDEN_PRODUCTION_TOKENS = Set.of(
      "navigationsimulation", "simulation_enabled", "simulation", "diagnosticruntime",
      "diagnosticsampler", "offlinecapturestore", "fieldacceptancehooks", "onlane",
      "navilaneitem");
  private static final Map<String, Integer> REQUIRED_GLOBAL_PRODUCTION_HOOKS = Map.of(
      "onStatus(Ljava/lang/Object;)V", 1,
      "onCurrentTbt(Ljava/lang/Object;)V", 1,
      "onNextTbt(Ljava/lang/Object;)V", 1,
      "onSafetySource(Ljava/lang/Object;)V", 1,
      "onSafety(Ljava/lang/Object;)V", 1,
      "onRoute(Ljava/lang/Object;)V", 1);

  private DexPatchMain() {}

  public static void main(String[] arguments) throws IOException {
    if (arguments.length > 0 && arguments[0].equals("verify-production")) {
      verifyProduction(arguments);
      return;
    }
    if (arguments.length > 0 && arguments[0].equals("patch")) {
      patch(arguments);
      return;
    }
    if (arguments.length < 2) {
      throw new IllegalArgumentException("usage: DexPatchMain <apk> <encoded-rule>...");
    }
    List<AnchorRule> rules = Arrays.stream(arguments).skip(1).map(DexPatchMain::decodeRule).toList();
    Set<String> names = new HashSet<>();
    if (rules.stream().map(AnchorRule::name).anyMatch(name -> !names.add(name))) {
      throw new IllegalArgumentException("anchor rule names must be unique");
    }

    MultiDexContainer<?> container = DexFileFactory.loadDexContainer(
        new File(arguments[0]), Opcodes.getDefault());
    List<DexUnit> units = new ArrayList<>();
    for (String entryName : container.getDexEntryNames()) {
      MultiDexContainer.DexEntry<?> entry = container.getEntry(entryName);
      if (entry == null) {
        throw new IllegalArgumentException("DEX entry disappeared: " + entryName);
      }
      units.add(new DexUnit(entryName, entry.getDexFile()));
    }
    Map<String, Integer> counts = DexAnchorScanner.scan(List.copyOf(units), rules);
    for (AnchorRule rule : rules) {
      System.out.println(rule.name() + "\t" + counts.get(rule.name()));
    }
  }

  static AnchorRule decodeRule(String encoded) {
    final String decoded;
    try {
      decoded = new String(Base64.getUrlDecoder().decode(encoded), StandardCharsets.UTF_8);
    } catch (IllegalArgumentException error) {
      throw new IllegalArgumentException("anchor rule must be URL-safe Base64", error);
    }
    String[] fields = decoded.split(FIELD_SEPARATOR, -1);
    if (fields.length < 5) {
      throw new IllegalArgumentException("anchor rule must contain identity and instruction fields");
    }
    return new AnchorRule(
        fields[0], fields[1], fields[2], fields[3], List.of(fields).subList(4, fields.length));
  }

  static PatchRule decodePatchRule(String encoded) {
    String decoded = decodeBase64(encoded);
    String[] fields = decoded.split(FIELD_SEPARATOR, -1);
    if (fields.length < 12) {
      throw new IllegalArgumentException("patch rule is incomplete");
    }
    int windowSize = parseIndex(fields[4], "window size");
    int metadataStart = 5 + windowSize;
    if (windowSize == 0 || fields.length != metadataStart + 6) {
      throw new IllegalArgumentException("patch rule window or hook metadata is malformed");
    }
    AnchorRule anchor = new AnchorRule(
        fields[0], fields[1], fields[2], fields[3], List.of(fields).subList(5, metadataStart));
    String hookDescriptor = fields[metadataStart + 1];
    int opening = hookDescriptor.indexOf('(');
    if (opening <= 0 || !hookDescriptor.substring(opening).equals("(Ljava/lang/Object;)V")) {
      throw new IllegalArgumentException("diagnostic hook descriptor must accept one Object and return void");
    }
    HookPlacement placement = switch (fields[metadataStart + 2]) {
      case "method_entry" -> HookPlacement.methodEntry();
      case "before_instruction" -> HookPlacement.beforeInstruction(
          parseIndex(fields[metadataStart + 3], "placement index"));
      case "after_instruction" -> HookPlacement.afterInstruction(
          parseIndex(fields[metadataStart + 3], "placement index"));
      default -> throw new IllegalArgumentException("unsupported hook placement");
    };
    HookArgument argument = switch (fields[metadataStart + 4]) {
      case "parameter" -> HookArgument.parameter(parseIndex(fields[metadataStart + 5], "argument index"));
      case "window_register" -> HookArgument.windowRegister(
          parseIndex(fields[metadataStart + 5], "argument index"));
      default -> throw new IllegalArgumentException("unsupported hook argument");
    };
    return new PatchRule(
        anchor,
        new ImmutableMethodReference(
            fields[metadataStart], hookDescriptor.substring(0, opening), List.of("Ljava/lang/Object;"), "V"),
        placement,
        argument);
  }

  static void verifyProductionInventory(
      Set<String> topLevelTypes, Set<String> references, String declaredBuildId) {
    if (!topLevelTypes.equals(PRODUCTION_TOP_LEVEL_TYPES)) {
      throw new IllegalArgumentException("production payload class inventory differs");
    }
    Set<String> productionBuildIds = references.stream()
        .filter(reference -> reference.startsWith("naver-6.8.0.5-"))
        .collect(Collectors.toSet());
    if (!PRODUCTION_BUILD_ID.equals(declaredBuildId)
        || !productionBuildIds.equals(Set.of(PRODUCTION_BUILD_ID))) {
      throw new IllegalArgumentException("production payload build identity differs");
    }
    String inventory = String.join("\n", references).toLowerCase();
    for (String forbidden : FORBIDDEN_PRODUCTION_TOKENS) {
      if (inventory.contains(forbidden)) {
        throw new IllegalArgumentException("forbidden production payload token: " + forbidden);
      }
    }
  }

  static void verifyProductionBindingCounts(Map<String, Integer> counts) {
    if (!counts.keySet().equals(PRODUCTION_BINDINGS.keySet())
        || counts.values().stream().anyMatch(count -> count != 1)) {
      throw new IllegalArgumentException("production hook binding counts differ");
    }
  }

  static void verifyGlobalProductionHookReferences(
      Map<String, Integer> productionCounts, int diagnosticCount, int fieldAcceptanceCount) {
    if (!productionCounts.equals(REQUIRED_GLOBAL_PRODUCTION_HOOKS)
        || diagnosticCount != 0 || fieldAcceptanceCount != 0) {
      throw new IllegalArgumentException("global production hook reference inventory differs");
    }
  }

  static void verifyExactProductionRules(List<PatchRule> rules) {
    Map<String, PatchRule> actual = new LinkedHashMap<>();
    for (PatchRule rule : rules) {
      if (actual.put(rule.anchor().name(), rule) != null) {
        throw new IllegalArgumentException("production patch rule names must be unique");
      }
    }
    if (!actual.keySet().equals(PRODUCTION_RULES.keySet())) {
      throw new IllegalArgumentException("production patch rule inventory differs");
    }
    for (Map.Entry<String, PatchRule> expected : PRODUCTION_RULES.entrySet()) {
      if (!samePatchRule(actual.get(expected.getKey()), expected.getValue())) {
        throw new IllegalArgumentException("production patch rule differs: " + expected.getKey());
      }
    }
  }

  static void verifyExactFinalHooks(List<DexUnit> units, List<PatchRule> rules) {
    Set<String> names = new HashSet<>();
    if (rules.isEmpty() || rules.stream().map(rule -> rule.anchor().name()).anyMatch(name -> !names.add(name))) {
      throw new IllegalArgumentException("final hook rules must be non-empty and unique");
    }
    for (PatchRule rule : rules) {
      List<LocatedMethod> targets = new ArrayList<>();
      for (DexUnit unit : units) {
        if (!unit.entryName().equals(rule.anchor().dexEntry())) continue;
        for (ClassDef classDef : unit.dexFile().getClasses()) {
          if (!classDef.getType().equals(rule.anchor().classDescriptor())) continue;
          for (Method method : classDef.getMethods()) {
            if (DexAnchorScanner.shortDescriptor(method).equals(rule.anchor().methodDescriptor())) {
              targets.add(new LocatedMethod(unit.entryName(), classDef, method));
            }
          }
        }
      }
      if (targets.size() != 1 || targets.get(0).method().getImplementation() == null) {
        throw new IllegalArgumentException("exact final hook target differs: " + rule.anchor().name());
      }
      verifyExactFinalHook(targets.get(0).method(), rule);
    }
  }

  private static void verifyProduction(String[] arguments) throws IOException {
    if (arguments.length != 9) {
      throw new IllegalArgumentException(
          "usage: DexPatchMain verify-production <apk> <payload-entry> <rule> <rule> <rule> <rule> <rule> <rule>");
    }
    if (!arguments[2].equals("classes43.dex")) {
      throw new IllegalArgumentException("production payload entry must be classes43.dex");
    }
    MultiDexContainer<?> container = DexFileFactory.loadDexContainer(
        new File(arguments[1]), Opcodes.getDefault());
    List<DexUnit> units = loadUnits(container);
    List<PatchRule> rules = Arrays.stream(arguments).skip(3).map(DexPatchMain::decodePatchRule).toList();
    verifyExactProductionRules(rules);
    DexUnit payloadUnit = units.stream().filter(unit -> unit.entryName().equals(arguments[2]))
        .findFirst().orElseThrow(() -> new IllegalArgumentException("production payload DEX is absent"));
    Set<String> topLevelTypes = payloadUnit.dexFile().getClasses().stream()
        .map(ClassDef::getType)
        .map(type -> type.indexOf('$') >= 0 ? type.substring(0, type.indexOf('$')) + ";" : type)
        .collect(Collectors.toSet());
    verifyProductionInventory(
        topLevelTypes,
        dexReferences(payloadUnit.dexFile()),
        productionHooksBuildId(payloadUnit.dexFile()));
    verifyProductionBindings(units);
    verifyGlobalHookReferences(units);
    verifyExactFinalHooks(units, rules);
    System.out.println("production_composition\t1");
    System.out.println("production_bindings\t1");
    System.out.println("production_global_hooks\t1");
    System.out.println("production_exact_hooks\t1");
  }

  private static void verifyExactFinalHook(Method method, PatchRule rule) {
    List<Instruction> finalInstructions = toList(method.getImplementation().getInstructions());
    List<IndexedInstruction> original = new ArrayList<>();
    List<Integer> hookIndices = new ArrayList<>();
    for (int index = 0; index < finalInstructions.size(); index++) {
      Instruction instruction = finalInstructions.get(index);
      if (isProductionHookReference(instruction)) {
        if (isExactHookReference(instruction, rule.hook())) hookIndices.add(index);
      } else {
        original.add(new IndexedInstruction(index, instruction));
      }
    }
    List<String> normalized = original.stream()
        .map(item -> normalizeInstruction(item.instruction())).toList();
    List<Integer> matches = findWindows(normalized, rule.anchor().instructionWindow());
    if (matches.size() != 1 || hookIndices.size() != 1) {
      throw new IllegalArgumentException("final hook/window exact-one mismatch: " + rule.anchor().name());
    }
    int matchStart = matches.get(0);
    int expectedBoundary = switch (rule.placement().kind()) {
      case METHOD_ENTRY -> 0;
      case BEFORE_INSTRUCTION -> matchStart + rule.placement().instructionIndex();
      case AFTER_INSTRUCTION -> matchStart + rule.placement().instructionIndex() + 1;
    };
    int hookIndex = hookIndices.get(0);
    long actualBoundary = original.stream().filter(item -> item.finalIndex() < hookIndex).count();
    Instruction hookInstruction = finalInstructions.get(hookIndex);
    if (actualBoundary != expectedBoundary
        || hookInstruction.getOpcode() != Opcode.INVOKE_STATIC_RANGE
        || !(hookInstruction instanceof RegisterRangeInstruction range)
        || range.getRegisterCount() != 1) {
      throw new IllegalArgumentException("final hook placement/opcode differs: " + rule.anchor().name());
    }
    int expectedRegister = switch (rule.argument().kind()) {
      case PARAMETER -> explicitParameterRegister(method, rule.argument().index());
      case WINDOW_REGISTER -> {
        Instruction source = original.get(matchStart + rule.argument().index()).instruction();
        if (!(source instanceof OneRegisterInstruction oneRegister)) {
          throw new IllegalArgumentException("final hook source is not one-register: " + rule.anchor().name());
        }
        yield oneRegister.getRegisterA();
      }
    };
    if (range.getStartRegister() != expectedRegister || !isReachable(finalInstructions, hookIndex)) {
      throw new IllegalArgumentException("final hook register/reachability differs: " + rule.anchor().name());
    }
  }

  private static boolean isProductionHookReference(Instruction instruction) {
    return instruction instanceof ReferenceInstruction referenced
        && referenced.getReference() instanceof MethodReference target
        && target.getDefiningClass().equals("Lai/comma/naver/payload/ProductionHooks;");
  }

  private static boolean isExactHookReference(Instruction instruction, MethodReference expected) {
    if (!(instruction instanceof ReferenceInstruction referenced)
        || !(referenced.getReference() instanceof MethodReference actual)) return false;
    return actual.getDefiningClass().equals(expected.getDefiningClass())
        && shortDescriptor(actual).equals(shortDescriptor(expected));
  }

  private static String normalizeInstruction(Instruction instruction) {
    String token = instruction.getOpcode().name;
    if (instruction instanceof ReferenceInstruction referenced) {
      token += " " + com.android.tools.smali.dexlib2.util.ReferenceUtil.getReferenceString(
          referenced.getReference());
    }
    return token;
  }

  private static List<Integer> findWindows(List<String> instructions, List<String> window) {
    List<Integer> matches = new ArrayList<>();
    for (int start = 0; start + window.size() <= instructions.size(); start++) {
      if (instructions.subList(start, start + window.size()).equals(window)) matches.add(start);
    }
    return List.copyOf(matches);
  }

  private static int explicitParameterRegister(Method method, int selectedIndex) {
    List<? extends CharSequence> types = method.getParameterTypes();
    if (selectedIndex >= types.size()) {
      throw new IllegalArgumentException("final hook parameter index is out of range");
    }
    int parameterWidth = (method.getAccessFlags() & 0x8) == 0 ? 1 : 0;
    for (CharSequence type : types) parameterWidth += isWide(type) ? 2 : 1;
    int register = method.getImplementation().getRegisterCount() - parameterWidth;
    if ((method.getAccessFlags() & 0x8) == 0) register++;
    for (int index = 0; index < selectedIndex; index++) {
      register += isWide(types.get(index)) ? 2 : 1;
    }
    return register;
  }

  private static boolean isWide(CharSequence type) {
    return type.length() == 1 && (type.charAt(0) == 'J' || type.charAt(0) == 'D');
  }

  private static boolean isReachable(List<Instruction> instructions, int targetIndex) {
    if (instructions.isEmpty()) return false;
    List<Integer> addresses = new ArrayList<>();
    Map<Integer, Integer> addressToIndex = new HashMap<>();
    int address = 0;
    for (int index = 0; index < instructions.size(); index++) {
      addresses.add(address);
      addressToIndex.put(address, index);
      address += instructions.get(index).getCodeUnits();
    }
    boolean[] reached = new boolean[instructions.size()];
    Deque<Integer> pending = new ArrayDeque<>();
    pending.add(0);
    while (!pending.isEmpty()) {
      int index = pending.removeFirst();
      if (index < 0 || index >= instructions.size() || reached[index]) continue;
      reached[index] = true;
      Instruction instruction = instructions.get(index);
      Opcode opcode = instruction.getOpcode();
      if (instruction instanceof OffsetInstruction offset && isControlFlowOffset(opcode)) {
        addAddress(pending, addressToIndex, addresses.get(index) + offset.getCodeOffset());
        if ((opcode == Opcode.PACKED_SWITCH || opcode == Opcode.SPARSE_SWITCH)
            && addressToIndex.get(addresses.get(index) + offset.getCodeOffset()) != null) {
          Instruction payload = instructions.get(
              addressToIndex.get(addresses.get(index) + offset.getCodeOffset()));
          if (payload instanceof SwitchPayload switchPayload) {
            switchPayload.getSwitchElements().forEach(element ->
                addAddress(pending, addressToIndex, addresses.get(index) + element.getOffset()));
          }
        }
      }
      if (opcode.canContinue()) pending.add(index + 1);
    }
    return reached[targetIndex];
  }

  private static boolean isControlFlowOffset(Opcode opcode) {
    return opcode == Opcode.GOTO || opcode == Opcode.GOTO_16 || opcode == Opcode.GOTO_32
        || opcode == Opcode.PACKED_SWITCH || opcode == Opcode.SPARSE_SWITCH
        || opcode.name.startsWith("if-");
  }

  private static void addAddress(
      Deque<Integer> pending, Map<Integer, Integer> addressToIndex, int address) {
    Integer index = addressToIndex.get(address);
    if (index != null) pending.add(index);
  }

  private static PatchRule productionRule(
      String name, String classDescriptor, String methodDescriptor, List<String> window,
      String hookName, HookPlacement placement, HookArgument argument) {
    return new PatchRule(
        new AnchorRule(name, "classes11.dex", classDescriptor, methodDescriptor, window),
        new ImmutableMethodReference(
            "Lai/comma/naver/payload/ProductionHooks;", hookName,
            List.of("Ljava/lang/Object;"), "V"),
        placement, argument);
  }

  private static boolean samePatchRule(PatchRule actual, PatchRule expected) {
    return actual != null
        && actual.anchor().equals(expected.anchor())
        && actual.placement().equals(expected.placement())
        && actual.argument().equals(expected.argument())
        && actual.hook().getDefiningClass().equals(expected.hook().getDefiningClass())
        && shortDescriptor(actual.hook()).equals(shortDescriptor(expected.hook()));
  }

  private static Set<String> dexReferences(DexFile dexFile) {
    Set<String> references = new HashSet<>();
    if (dexFile instanceof DexBackedDexFile backed) {
      backed.getStringReferences().forEach(reference -> references.add(reference.getString()));
    }
    for (ClassDef classDef : dexFile.getClasses()) {
      references.add(classDef.getType());
      if (classDef.getSuperclass() != null) references.add(classDef.getSuperclass());
      references.addAll(classDef.getInterfaces());
      classDef.getFields().forEach(field -> {
        references.add(field.getName());
        references.add(field.getType());
        if (field.getInitialValue() != null) references.add(field.getInitialValue().toString());
      });
      for (Method method : classDef.getMethods()) {
        references.add(method.getName());
        references.add(DexAnchorScanner.shortDescriptor(method));
        if (method.getImplementation() == null) continue;
        for (Instruction instruction : method.getImplementation().getInstructions()) {
          if (instruction instanceof ReferenceInstruction referenced) {
            references.add(referenced.getReference().toString());
          }
        }
      }
    }
    return Set.copyOf(references);
  }

  private static String productionHooksBuildId(DexFile dexFile) {
    List<String> values = new ArrayList<>();
    for (ClassDef classDef : dexFile.getClasses()) {
      if (!classDef.getType().equals("Lai/comma/naver/payload/ProductionHooks;")) {
        continue;
      }
      classDef.getFields().forEach(field -> {
        if (field.getName().equals("PAYLOAD_BUILD_ID")
            && field.getType().equals("Ljava/lang/String;")
            && (field.getAccessFlags() & 0x19) == 0x19
            && field.getInitialValue() instanceof StringEncodedValue value) {
          values.add(value.getValue());
        }
      });
    }
    if (values.size() != 1) {
      throw new IllegalArgumentException("production payload build identity field differs");
    }
    return values.get(0);
  }

  private static void verifyGlobalHookReferences(List<DexUnit> units) {
    Map<String, Integer> production = new java.util.HashMap<>();
    int diagnostic = 0;
    int fieldAcceptance = 0;
    for (DexUnit unit : units) {
      for (ClassDef classDef : unit.dexFile().getClasses()) {
        for (Method method : classDef.getMethods()) {
          if (method.getImplementation() == null) continue;
          for (Instruction instruction : method.getImplementation().getInstructions()) {
            if (!(instruction instanceof ReferenceInstruction referenced)
                || !(referenced.getReference() instanceof MethodReference target)) continue;
            if (target.getDefiningClass().equals("Lai/comma/naver/payload/DiagnosticHooks;")) {
              diagnostic++;
            } else if (target.getDefiningClass().equals(
                "Lai/comma/naver/payload/FieldAcceptanceHooks;")) {
              fieldAcceptance++;
            } else if (target.getDefiningClass().equals("Lai/comma/naver/payload/ProductionHooks;")) {
              StringBuilder descriptor = new StringBuilder(target.getName()).append('(');
              target.getParameterTypes().forEach(descriptor::append);
              descriptor.append(')').append(target.getReturnType());
              production.merge(descriptor.toString(), 1, Integer::sum);
            }
          }
        }
      }
    }
    verifyGlobalProductionHookReferences(Map.copyOf(production), diagnostic, fieldAcceptance);
  }

  private static void verifyProductionBindings(List<DexUnit> units) {
    Map<String, Integer> counts = new java.util.HashMap<>();
    PRODUCTION_BINDINGS.keySet().forEach(name -> counts.put(name, 0));
    for (DexUnit unit : units) {
      for (ClassDef classDef : unit.dexFile().getClasses()) {
        for (Method method : classDef.getMethods()) {
          if (method.getImplementation() == null) continue;
          for (Map.Entry<String, List<String>> binding : PRODUCTION_BINDINGS.entrySet()) {
            List<String> contract = binding.getValue();
            if (!classDef.getType().equals(contract.get(0))
                || !DexAnchorScanner.shortDescriptor(method).equals(contract.get(1))) continue;
            for (Instruction instruction : method.getImplementation().getInstructions()) {
              if (instruction instanceof ReferenceInstruction referenced
                  && referenced.getReference() instanceof MethodReference target
                  && target.getDefiningClass().equals("Lai/comma/naver/payload/ProductionHooks;")
                  && shortDescriptor(target).equals(contract.get(2))) {
                counts.merge(binding.getKey(), 1, Integer::sum);
              }
            }
          }
        }
      }
    }
    verifyProductionBindingCounts(Map.copyOf(counts));
  }

  private static String shortDescriptor(MethodReference method) {
    StringBuilder descriptor = new StringBuilder(method.getName()).append('(');
    method.getParameterTypes().forEach(descriptor::append);
    return descriptor.append(')').append(method.getReturnType()).toString();
  }

  private static void patch(String[] arguments) throws IOException {
    if (arguments.length < 7) {
      throw new IllegalArgumentException(
          "usage: DexPatchMain patch <apk> <output-dir> <payload-dex> <payload-entry> <hook-class> <rule>...");
    }
    File apk = new File(arguments[1]);
    Path output = Path.of(arguments[2]).toAbsolutePath().normalize();
    Path payloadDex = Path.of(arguments[3]).toAbsolutePath().normalize();
    String payloadEntry = arguments[4];
    String hookClass = arguments[5];
    List<PatchRule> rules = Arrays.stream(arguments).skip(6).map(DexPatchMain::decodePatchRule).toList();
    if (Files.exists(output)) {
      throw new IllegalArgumentException("patch output must not already exist");
    }
    List<DexUnit> units = loadUnits(apk);
    if (units.stream().anyMatch(unit -> unit.entryName().equals(payloadEntry))) {
      throw new IllegalArgumentException("payload DEX entry collides with the base APK");
    }
    DexFile payload = DexFileFactory.loadDexFile(payloadDex.toFile(), Opcodes.getDefault());
    if (payload.getClasses().stream().noneMatch(classDef -> classDef.getType().equals(hookClass))) {
      throw new IllegalArgumentException("payload DEX does not contain the configured hook class");
    }
    Map<String, DexFile> patched = DexPatchEngine.patchExact(units, rules);
    Path parent = output.getParent();
    if (parent == null) {
      throw new IllegalArgumentException("patch output must have a parent directory");
    }
    Files.createDirectories(parent);
    Path staging = Files.createTempDirectory(parent, ".diagnostic-patch-");
    try {
      for (Map.Entry<String, DexFile> item : patched.entrySet()) {
        DexFileFactory.writeDexFile(staging.resolve(item.getKey()).toString(), item.getValue());
      }
      Files.copy(payloadDex, staging.resolve(payloadEntry), StandardCopyOption.COPY_ATTRIBUTES);
      Files.move(staging, output, StandardCopyOption.ATOMIC_MOVE);
    } finally {
      if (Files.exists(staging)) {
        try (var paths = Files.walk(staging)) {
          for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
            Files.deleteIfExists(path);
          }
        }
      }
    }
    for (PatchRule rule : rules) {
      System.out.println(rule.anchor().name() + "\t1");
    }
  }

  private static List<DexUnit> loadUnits(File apk) throws IOException {
    MultiDexContainer<?> container = DexFileFactory.loadDexContainer(apk, Opcodes.getDefault());
    return loadUnits(container);
  }

  private static List<DexUnit> loadUnits(MultiDexContainer<?> container) throws IOException {
    List<DexUnit> units = new ArrayList<>();
    for (String entryName : container.getDexEntryNames()) {
      MultiDexContainer.DexEntry<?> entry = container.getEntry(entryName);
      if (entry == null) {
        throw new IllegalArgumentException("DEX entry disappeared: " + entryName);
      }
      units.add(new DexUnit(entryName, entry.getDexFile()));
    }
    return List.copyOf(units);
  }

  private static int parseIndex(String raw, String label) {
    try {
      int result = Integer.parseInt(raw);
      if (result < 0) {
        throw new NumberFormatException();
      }
      return result;
    } catch (NumberFormatException error) {
      throw new IllegalArgumentException(label + " must be a non-negative integer", error);
    }
  }

  private static String decodeBase64(String encoded) {
    try {
      return new String(Base64.getUrlDecoder().decode(encoded), StandardCharsets.UTF_8);
    } catch (IllegalArgumentException error) {
      throw new IllegalArgumentException("rule must be URL-safe Base64", error);
    }
  }

  private static <T> List<T> toList(Iterable<? extends T> values) {
    List<T> result = new ArrayList<>();
    values.forEach(result::add);
    return List.copyOf(result);
  }

  private record LocatedMethod(String dexEntry, ClassDef classDef, Method method) {}

  private record IndexedInstruction(int finalIndex, Instruction instruction) {}
}

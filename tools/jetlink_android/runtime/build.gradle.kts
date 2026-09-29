plugins { kotlin("jvm") }
java { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
kotlin { compilerOptions.jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
dependencies {
    implementation(project(":core"))
    compileOnly("com.microsoft.onnxruntime:onnxruntime:1.22.0")
    testImplementation("com.microsoft.onnxruntime:onnxruntime:1.22.0")
    testImplementation("org.json:json:20240303")
    testImplementation("junit:junit:4.13.2")
}
tasks.test {
    useJUnit()
    systemProperty("jetlink.fixtures", layout.projectDirectory.dir("../../jetlink_model/fixtures").asFile.path)
}

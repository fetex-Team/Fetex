using System;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering.HighDefinition;
using UnityEngine.SceneManagement;
using Fetex;

// 제공 프리팹으로 검토용 도시 씬을 만들고 실제 월드 치수를 기록한다.
public static class KakaoSceneSetup
{
    public const string ScenePath = "Assets/Fetex/Scenes/KakaoDigitalTwin.unity";
    const string ReportPath = "Documentation/KakaoSceneMeasurements.txt";

    [MenuItem("Fetex/Create Kakao Digital Twin Scene")]
    public static void Create()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode || File.Exists(ScenePath)) return;
        try
        {
            Directory.CreateDirectory("Assets/Fetex/Scenes");
            Directory.CreateDirectory("Documentation");
            var previous = SceneManager.GetActiveScene();
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            SceneManager.SetActiveScene(scene);
            var city = Spawn("Assets/Map/street_main_1.prefab", scene);
            var environment = Spawn("Assets/effect/Kakao_MapSetting.prefab", scene);
            var taxi = Spawn("Assets/object/car_taxi_1.prefab", scene);
            taxi.name = "Reference Taxi";
            // 원본 배치 좌표를 유지해야 환경·도시·차량의 기준이 일치한다.
            foreach (var animator in taxi.GetComponentsInChildren<Animator>()) animator.enabled = false;
            var bounds = Measure(city);
            var cameraObject = new GameObject("Main Camera", typeof(Camera), typeof(AudioListener), typeof(HDAdditionalCameraData));
            var camera = cameraObject.GetComponent<Camera>();
            camera.tag = "MainCamera";
            camera.nearClipPlane = 0.3f;
            camera.farClipPlane = 2500f;
            camera.fieldOfView = 55f;
            camera.transform.position = bounds.center + new Vector3(0, Mathf.Max(bounds.size.x, bounds.size.z), 0);
            camera.transform.rotation = Quaternion.Euler(90, 0, 0);
            if (!environment.GetComponentsInChildren<Light>().Any(l => l.type == LightType.Directional))
            {
                var sun = new GameObject("Sun").AddComponent<Light>();
                sun.type = LightType.Directional;
                sun.transform.rotation = Quaternion.Euler(50, -30, 0);
            }
            EditorSceneManager.SaveScene(scene, ScenePath);
            if (previous.IsValid() && !previous.isDirty) EditorSceneManager.CloseScene(previous, true);
            Selection.activeGameObject = city;
            if (SceneView.lastActiveSceneView != null) SceneView.lastActiveSceneView.Frame(bounds, true);
            ConfigureViews();
            SetupReplay();
            AssetDatabase.Refresh();
            Debug.Log("Fetex: KakaoDigitalTwin scene created and Replay configured; measurements saved.");
        }
        catch (Exception e)
        {
            Directory.CreateDirectory("Documentation");
            File.WriteAllText("Documentation/KakaoSetupError.txt", e.ToString());
            Debug.LogException(e);
        }
    }

    static GameObject Spawn(string path, Scene scene)
    {
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
        if (prefab == null) throw new InvalidOperationException("Missing prefab: " + path);
        return (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
    }

    static Bounds Measure(GameObject root)
    {
        var renderers = root.GetComponentsInChildren<Renderer>();
        if (renderers.Length == 0) throw new InvalidOperationException("No renderers: " + root.name);
        var bounds = renderers[0].bounds;
        foreach (var renderer in renderers) bounds.Encapsulate(renderer.bounds);
        return bounds;
    }

    [MenuItem("Fetex/Configure Review Cameras")]
    public static void ConfigureViews()
    {
        var scene = SceneManager.GetSceneByPath(ScenePath);
        if (!scene.IsValid() || !scene.isLoaded) throw new InvalidOperationException("Open KakaoDigitalTwin first.");
        var taxi = scene.GetRootGameObjects().FirstOrDefault(r => r.name == "Reference Taxi");
        if (taxi != null)
        {
            // 북쪽 진행 차선의 검토 위치. SUMO 연동 시 상태 좌표로 대체한다.
            taxi.transform.SetPositionAndRotation(new Vector3(88f, 0.02f, -130f), Quaternion.identity);
        }
        var camera = scene.GetRootGameObjects().Select(r => r.GetComponent<Camera>()).FirstOrDefault(c => c != null);
        if (camera != null)
        {
            var controller = camera.GetComponent<TwinCameraView>();
            if (controller == null) controller = camera.gameObject.AddComponent<TwinCameraView>();
            if (taxi != null) controller.target = taxi.transform;
            controller.overview = true;
            controller.ApplyView();
        }
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        WriteMeasurements();
        Debug.Log("Fetex: overview/follow cameras configured.");
    }

    [MenuItem("Fetex/Setup Simulation Replay")]
    public static void SetupReplay()
    {
        var scene = SceneManager.GetSceneByPath(ScenePath);
        if (!scene.IsValid() || !scene.isLoaded)
        {
            if (File.Exists(ScenePath))
            {
                scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
            }
            else
            {
                Create();
                scene = SceneManager.GetSceneByPath(ScenePath);
            }
        }

        var playerObj = GameObject.Find("[ReplayManager]");
        if (playerObj == null)
        {
            playerObj = new GameObject("[ReplayManager]");
        }

        var player = playerObj.GetComponent<FetexReplayPlayer>();
        if (player == null)
        {
            player = playerObj.AddComponent<FetexReplayPlayer>();
        }

        player.taxiPrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/object/car_taxi_1.prefab");
        player.civilianCarPrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/object/obs_car_1.prefab");
        player.obstaclePrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/object/obs_kickboard_q.prefab");

        var camera = Camera.main;
        if (camera != null)
        {
            player.cameraView = camera.GetComponent<TwinCameraView>();
        }

        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Debug.Log("Fetex: [ReplayManager] successfully configured in KakaoDigitalTwin scene!");
    }

    [MenuItem("Fetex/Write Kakao Measurements")]
    public static void WriteMeasurements()
    {
        var scene = SceneManager.GetSceneByPath(ScenePath);
        if (!scene.IsValid()) scene = SceneManager.GetActiveScene();
        var report = new StringBuilder("Unity world-space measurements (metres if asset scale is 1m/unit)\n");
        foreach (var root in scene.GetRootGameObjects())
        {
            var renderers = root.GetComponentsInChildren<Renderer>();
            report.AppendLine(root.name + " position=" + root.transform.position.ToString("F3") + " scale=" + root.transform.lossyScale);
            if (renderers.Length == 0) continue;
            report.AppendLine("bounds=" + Measure(root).ToString("F3") + " renderers=" + renderers.Length);
            int missing = root.GetComponentsInChildren<Transform>(true).Sum(t => GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(t.gameObject));
            var materials = renderers.SelectMany(r => r.sharedMaterials).Where(m => m != null).Distinct().ToArray();
            report.AppendLine("missingScripts=" + missing + " materials=" + materials.Length);
            foreach (var material in materials.Where(m => m.shader == null || !m.shader.isSupported)) report.AppendLine("UNSUPPORTED: " + material.name);
            foreach (var r in renderers.Where(r => (r.bounds.size.y < 1 && r.bounds.size.x * r.bounds.size.z > 100) || root.name == "Reference Taxi"))
                report.AppendLine(r.name + " center=" + r.bounds.center.ToString("F3") + " size=" + r.bounds.size.ToString("F3"));
        }
        Directory.CreateDirectory("Documentation");
        File.WriteAllText(ReportPath, report.ToString());
    }

    [MenuItem("Fetex/Capture Review")]
    public static void Capture()
    {
        var controller = Camera.main.GetComponent<TwinCameraView>();
        bool previous = controller.overview;
        try
        {
            controller.overview = true;
            controller.ApplyView();
            CaptureCamera("Documentation/KakaoOverview.png");
            controller.overview = false;
            controller.ApplyView();
            CaptureCamera("Documentation/KakaoFollow.png");
        }
        finally { controller.overview = previous; controller.ApplyView(); }
    }

    static void CaptureCamera(string path)
    {
        // 현재 검토 카메라를 이미지로 저장하고 렌더 타깃을 원상 복구한다.
        var camera = Camera.main;
        var target = RenderTexture.GetTemporary(1600, 1000, 24);
        var oldTarget = camera.targetTexture;
        var oldActive = RenderTexture.active;
        var texture = new Texture2D(1600, 1000, TextureFormat.RGB24, false);
        try
        {
            camera.targetTexture = target;
            camera.Render();
            RenderTexture.active = target;
            texture.ReadPixels(new Rect(0, 0, 1600, 1000), 0, 0);
            texture.Apply();
            File.WriteAllBytes(path, texture.EncodeToPNG());
        }
        finally
        {
            camera.targetTexture = oldTarget;
            RenderTexture.active = oldActive;
            RenderTexture.ReleaseTemporary(target);
            UnityEngine.Object.DestroyImmediate(texture);
        }
    }
}

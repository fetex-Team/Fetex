code = '''using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using UnityEngine;
using UnityEngine.Rendering;
#if UNITY_EDITOR
using UnityEditor;
#endif

namespace Fetex
{
    // ==============================================================
    // 1. JSON Data Transfer Objects (SUMO / export_unity_replay.py)
    // ==============================================================
    [Serializable]
    public class ReplayData
    {
        public int version;
        public string strategy;
        public string algorithm;
        public string fingerprint;
        public float duration;
        public int startHour;
        public int stride;
        public List<EntityData> entities;
        public List<FrameData> frames;
    }

    [Serializable]
    public class EntityData
    {
        public string id;
        public string kind;
    }

    [Serializable]
    public class FrameData
    {
        public float t;
        public List<VehicleState> vehicles;
        public List<PersonPos> people;
        public int requested;
        public int picked;
        public int waiting;
        public int arrived;
        public int timedOut;
        public float avgWait;
        public int moves;
        public int teleports;
        public int forecast;
    }

    [Serializable]
    public class VehicleState
    {
        public int i;
        public float x;
        public float z;
        public float a;
        public int s;
    }

    [Serializable]
    public class PersonPos
    {
        public float x;
        public float z;
    }

    [Serializable]
    public class MapData
    {
        public int version;
        public string source;
        public float width;
        public float height;
        public float offsetX;
        public float offsetY;
        public bool grid;
        public List<RoadData> roads;
        public List<JunctionData> junctions;
        public List<CellData> cells;
    }

    [Serializable]
    public class RoadData
    {
        public string id;
        public float width;
        public List<Point2D> points;
    }

    [Serializable]
    public class JunctionData
    {
        public string id;
        public List<Point2D> points;
    }

    [Serializable]
    public class CellData
    {
        public string id;
        public Point2D center;
        public List<Point2D> points;
    }

    [Serializable]
    public class Point2D
    {
        public float x;
        public float z;
    }

    // ==============================================================
    // 2. Replay Player Engine & UI Controller
    // ==============================================================
    public class FetexReplayPlayer : MonoBehaviour
    {
        public static FetexReplayPlayer Instance { get; private set; }

        [Header("3D Prefabs")]
        public GameObject taxiPrefab;
        public GameObject civilianCarPrefab;
        public GameObject obstaclePrefab;

        [Header("Simulation Playback")]
        public string replayFileName = "rl_prepositioned.json";
        public bool autoPlayOnStart = true;
        [Range(0.1f, 100f)] public float playbackSpeed = 5.0f;
        public float groundY = 0.05f;

        [Header("Real Gangnam Map & City")]
        public bool showGangnamRoadNetwork = true;
        public bool showGangnamBuildings = true;
        public bool showH3Cells = true;
        public bool showSample3x3City = false;

        [Header("Live State")]
        public bool isLoaded = false;
        public bool isPlaying = false;
        public float currentTime = 0.0f;

        [Header("Camera Link")]
        public TwinCameraView cameraView;

        private ReplayData replayData;
        private MapData mapData;
        private GameObject roadNetworkObject;
        private GameObject buildingsContainerObject;
        private GameObject groundPlaneObject;
        private readonly List<GameObject> h3CellObjects = new List<GameObject>();
        private GameObject sampleCityObject;

        private readonly Dictionary<int, GameObject> vehicleInstances = new Dictionary<int, GameObject>();
        private readonly Dictionary<int, Renderer> statusMarkers = new Dictionary<int, Renderer>();
        private readonly Dictionary<int, int> lastVehicleStates = new Dictionary<int, int>();
        private readonly List<GameObject> personPool = new List<GameObject>();
        private readonly List<int> activeTaxiIndices = new List<int>();
        private int currentFollowTaxiIndex = 0;

        // Shared Materials
        private Material matIdle;
        private Material matPickup;
        private Material matOccupied;
        private Material matReposition;
        private Material matCivilian;
        private Material matAuto;
        private Material matObstacle;
        private Material matPerson;
        private Material matRoadAsphalt;
        private Material matGroundPlane;
        private Material matH3Boundary;
        private Material matTowerGlass;
        private Material matTowerConcrete;
        private Material matTowerDark;

        private GUIStyle headerStyle;
        private GUIStyle boldStyle;
        private GUIStyle statusStyle;
        private bool isStyleInitialized = false;

        public string CurrentStrategyName
        {
            get
            {
                if (replayData == null) return "None";
                if (replayData.strategy.Contains("rl")) return "강화학습 선제재배치 (PPO + Hungarian)";
                if (replayData.strategy.Contains("patrol")) return "일반 순찰 (Baseline Hungarian)";
                return replayData.strategy;
            }
        }

        public string CurrentClockString
        {
            get
            {
                if (replayData == null) return "00:00:00";
                float totalSeconds = (replayData.startHour * 3600f) + currentTime;
                int hours = Mathf.FloorToInt(totalSeconds / 3600f) % 24;
                int minutes = Mathf.FloorToInt((totalSeconds % 3600f) / 60f);
                int seconds = Mathf.FloorToInt(totalSeconds % 60f);
                return string.Format("{0:D2}:{1:D2}:{2:D2}", hours, minutes, seconds);
            }
        }

        private void Awake()
        {
            Instance = this;
            InitializeMaterials();

            if (cameraView == null)
            {
                cameraView = FindFirstObjectByType<TwinCameraView>();
            }

            FindSampleCityObject();

#if UNITY_EDITOR
            if (taxiPrefab == null)
                taxiPrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/object/car_taxi_1.prefab");
            if (civilianCarPrefab == null)
                civilianCarPrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/object/obs_car_1.prefab");
            if (obstaclePrefab == null)
                obstaclePrefab = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/object/obs_kickboard_q.prefab");
#endif
        }

        private void Start()
        {
            LoadMapAndReplay(replayFileName);
        }

        private void FindSampleCityObject()
        {
            sampleCityObject = GameObject.Find("street_main_1(Clone)");
            if (sampleCityObject == null) sampleCityObject = GameObject.Find("street_main_1");
            if (sampleCityObject == null)
            {
                var cityObj = GameObject.Find("City") ?? GameObject.Find("Map");
                if (cityObj != null) sampleCityObject = cityObj;
            }
        }

        private void InitializeMaterials()
        {
            Shader shader = Shader.Find("HDRP/Unlit");
            if (shader == null) shader = Shader.Find("HDRP/Lit");
            if (shader == null) shader = Shader.Find("Universal Render Pipeline/Unlit");
            if (shader == null) shader = Shader.Find("Standard");
            if (shader == null) shader = Shader.Find("Unlit/Color");

            matIdle = CreateStatusMaterial(shader, new Color(0.15f, 0.95f, 0.25f), "Mat_Idle");
            matPickup = CreateStatusMaterial(shader, new Color(1.0f, 0.85f, 0.1f), "Mat_Pickup");
            matOccupied = CreateStatusMaterial(shader, new Color(0.95f, 0.2f, 0.2f), "Mat_Occupied");
            matReposition = CreateStatusMaterial(shader, new Color(0.05f, 0.65f, 1.0f), "Mat_Reposition");
            matCivilian = CreateStatusMaterial(shader, new Color(0.7f, 0.7f, 0.7f), "Mat_Civilian");
            matAuto = CreateStatusMaterial(shader, new Color(1.0f, 0.5f, 0.05f), "Mat_Auto");
            matObstacle = CreateStatusMaterial(shader, new Color(0.85f, 0.15f, 0.95f), "Mat_Obstacle");
            matPerson = CreateStatusMaterial(shader, new Color(1.0f, 0.2f, 0.6f), "Mat_Person");

            matRoadAsphalt = CreateStatusMaterial(shader, new Color(0.12f, 0.13f, 0.16f), "Mat_RoadAsphalt");
            matGroundPlane = CreateStatusMaterial(shader, new Color(0.20f, 0.22f, 0.25f), "Mat_GroundPlane");
            matH3Boundary = CreateStatusMaterial(shader, new Color(0.0f, 0.9f, 1.0f, 0.7f), "Mat_H3Boundary");

            matTowerGlass = CreateStatusMaterial(shader, new Color(0.12f, 0.26f, 0.38f), "Mat_TowerGlass");
            matTowerConcrete = CreateStatusMaterial(shader, new Color(0.65f, 0.68f, 0.72f), "Mat_TowerConcrete");
            matTowerDark = CreateStatusMaterial(shader, new Color(0.22f, 0.24f, 0.28f), "Mat_TowerDark");
        }

        private Material CreateStatusMaterial(Shader shader, Color color, string name)
        {
            Material mat = new Material(shader) { name = name };
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Color")) mat.SetColor("_Color", color);
            if (mat.HasProperty("_UnlitColor")) mat.SetColor("_UnlitColor", color);
            return mat;
        }

        public void LoadMapAndReplay(string fileName)
        {
            replayFileName = fileName;
            StartCoroutine(LoadAllCoroutine(fileName));
        }

        private IEnumerator LoadAllCoroutine(string fileName)
        {
            isLoaded = false;
            isPlaying = false;
            ClearAllInstances();

            if (mapData == null)
            {
                string mapPath = Path.Combine(Application.streamingAssetsPath, "map.json");
                if (File.Exists(mapPath))
                {
                    string mapJson = null;
                    var mapTask = System.Threading.Tasks.Task.Run(() => File.ReadAllText(mapPath, System.Text.Encoding.UTF8));
                    while (!mapTask.IsCompleted) yield return null;

                    if (!mapTask.IsFaulted)
                    {
                        mapJson = mapTask.Result;
                        try { mapData = JsonUtility.FromJson<MapData>(mapJson); } catch { }
                    }
                }
            }

            if (mapData != null && roadNetworkObject == null)
            {
                BuildGangnamEnvironment(mapData);
            }

            string replayPath = Path.Combine(Application.streamingAssetsPath, fileName);
            if (!File.Exists(replayPath))
            {
                Debug.LogError($"[FetexReplayPlayer] File not found: {replayPath}");
                yield break;
            }

            string replayJson = null;
            var replayTask = System.Threading.Tasks.Task.Run(() => File.ReadAllText(replayPath, System.Text.Encoding.UTF8));
            while (!replayTask.IsCompleted) yield return null;

            if (replayTask.IsFaulted)
            {
                Debug.LogError($"[FetexReplayPlayer] Read error: {replayTask.Exception}");
                yield break;
            }

            replayJson = replayTask.Result;
            try
            {
                replayData = JsonUtility.FromJson<ReplayData>(replayJson);
            }
            catch (Exception ex)
            {
                Debug.LogError($"[FetexReplayPlayer] Parse error: {ex}");
                yield break;
            }

            if (replayData == null || replayData.frames == null || replayData.frames.Count == 0)
            {
                Debug.LogError("[FetexReplayPlayer] ReplayData has no valid frames!");
                yield break;
            }

            currentTime = 0f;
            isLoaded = true;
            isPlaying = autoPlayOnStart;

            activeTaxiIndices.Clear();
            if (replayData.entities != null)
            {
                for (int i = 0; i < replayData.entities.Count; i++)
                {
                    string k = replayData.entities[i].kind;
                    string id = replayData.entities[i].id;
                    if ((k != null && k.ToLower().Contains("taxi")) || (id != null && id.ToLower().Contains("taxi")))
                    {
                        activeTaxiIndices.Add(i);
                    }
                }
            }

            Debug.Log($"[FetexReplayPlayer] Loaded {fileName} ({replayData.frames.Count} frames) | Real Gangnam roads: {mapData?.roads?.Count ?? 0}");

            ApplyCityVisibility();
            UpdateSimulation(0f);
            SelectTaxiForCamera(0);
        }

        // ==============================================================
        // 3. Real Gangnam Map, Ground & 3D Building Generator
        // ==============================================================
        private void BuildGangnamEnvironment(MapData map)
        {
            if (map == null || map.roads == null) return;

            if (roadNetworkObject != null) Destroy(roadNetworkObject);
            if (groundPlaneObject != null) Destroy(groundPlaneObject);
            if (buildingsContainerObject != null) Destroy(buildingsContainerObject);
            foreach (var cellObj in h3CellObjects) if (cellObj != null) Destroy(cellObj);
            h3CellObjects.Clear();

            // 1. Create Urban Base Ground Plane
            groundPlaneObject = GameObject.CreatePrimitive(PrimitiveType.Plane);
            groundPlaneObject.name = "[GangnamGroundPlane]";
            groundPlaneObject.transform.SetParent(transform, false);
            groundPlaneObject.transform.position = new Vector3(0f, 0f, 0f);
            groundPlaneObject.transform.localScale = new Vector3(180f, 1f, 220f); // 1800m x 2200m
            Destroy(groundPlaneObject.GetComponent<Collider>());
            groundPlaneObject.GetComponent<Renderer>().sharedMaterial = matGroundPlane;

            // 2. Build Road & Junction Mesh
            roadNetworkObject = new GameObject("[GangnamRoadNetwork]");
            roadNetworkObject.transform.SetParent(transform, false);

            MeshFilter mf = roadNetworkObject.AddComponent<MeshFilter>();
            MeshRenderer mr = roadNetworkObject.AddComponent<MeshRenderer>();
            mr.sharedMaterial = matRoadAsphalt;

            Mesh mesh = new Mesh();
            mesh.name = "GangnamRoadsMesh";
            mesh.indexFormat = IndexFormat.UInt32;

            List<Vector3> vertices = new List<Vector3>();
            List<int> triangles = new List<int>();

            // Road ribbons with double-sided triangles and generous width
            for (int r = 0; r < map.roads.Count; r++)
            {
                RoadData road = map.roads[r];
                if (road.points == null || road.points.Count < 2) continue;

                float halfWidth = Mathf.Max(road.width * 0.55f, 2.4f);

                for (int p = 0; p < road.points.Count - 1; p++)
                {
                    Vector3 p0 = new Vector3(road.points[p].x, groundY + 0.02f, road.points[p].z);
                    Vector3 p1 = new Vector3(road.points[p + 1].x, groundY + 0.02f, road.points[p + 1].z);

                    Vector3 dir = (p1 - p0).normalized;
                    if (dir.sqrMagnitude < 0.001f) continue;

                    Vector3 normal = new Vector3(-dir.z, 0f, dir.x) * halfWidth;

                    int baseIdx = vertices.Count;
                    vertices.Add(p0 - normal);
                    vertices.Add(p0 + normal);
                    vertices.Add(p1 - normal);
                    vertices.Add(p1 + normal);

                    // Front face
                    triangles.Add(baseIdx + 0);
                    triangles.Add(baseIdx + 1);
                    triangles.Add(baseIdx + 2);

                    triangles.Add(baseIdx + 2);
                    triangles.Add(baseIdx + 1);
                    triangles.Add(baseIdx + 3);

                    // Back face (no backface culling)
                    triangles.Add(baseIdx + 2);
                    triangles.Add(baseIdx + 1);
                    triangles.Add(baseIdx + 0);

                    triangles.Add(baseIdx + 3);
                    triangles.Add(baseIdx + 1);
                    triangles.Add(baseIdx + 2);
                }
            }

            // Junctions (double-sided fans to guarantee visibility)
            if (map.junctions != null)
            {
                for (int j = 0; j < map.junctions.Count; j++)
                {
                    JunctionData junc = map.junctions[j];
                    if (junc.points == null || junc.points.Count < 3) continue;

                    Vector3 centroid = Vector3.zero;
                    for (int p = 0; p < junc.points.Count; p++)
                    {
                        centroid += new Vector3(junc.points[p].x, groundY + 0.025f, junc.points[p].z);
                    }
                    centroid /= junc.points.Count;

                    int centerIdx = vertices.Count;
                    vertices.Add(centroid);

                    int startPointIdx = vertices.Count;
                    for (int p = 0; p < junc.points.Count; p++)
                    {
                        vertices.Add(new Vector3(junc.points[p].x, groundY + 0.025f, junc.points[p].z));
                    }

                    for (int p = 0; p < junc.points.Count; p++)
                    {
                        int nextP = (p + 1) % junc.points.Count;
                        // CW
                        triangles.Add(centerIdx);
                        triangles.Add(startPointIdx + p);
                        triangles.Add(startPointIdx + nextP);

                        // CCW (Double-sided)
                        triangles.Add(centerIdx);
                        triangles.Add(startPointIdx + nextP);
                        triangles.Add(startPointIdx + p);
                    }
                }
            }

            mesh.SetVertices(vertices);
            mesh.SetTriangles(triangles, 0);
            mesh.RecalculateNormals();
            mesh.RecalculateBounds();
            mf.sharedMesh = mesh;

            // 3. Build H3 Hexagonal Cell Outlines
            if (map.cells != null)
            {
                for (int c = 0; c < map.cells.Count; c++)
                {
                    CellData cell = map.cells[c];
                    if (cell.points == null || cell.points.Count < 3) continue;

                    GameObject cellObj = new GameObject($"H3Cell_{cell.id}");
                    cellObj.transform.SetParent(roadNetworkObject.transform, false);

                    LineRenderer lr = cellObj.AddComponent<LineRenderer>();
                    lr.sharedMaterial = matH3Boundary;
                    lr.startWidth = 4.0f;
                    lr.endWidth = 4.0f;
                    lr.loop = true;
                    lr.useWorldSpace = true;
                    lr.positionCount = cell.points.Count;

                    for (int p = 0; p < cell.points.Count; p++)
                    {
                        lr.SetPosition(p, new Vector3(cell.points[p].x, groundY + 0.08f, cell.points[p].z));
                    }

                    h3CellObjects.Add(cellObj);
                }
            }

            // 4. Build Procedural Gangnam 3D Skyscraper Blocks
            BuildProceduralGangnamBuildings(map);

            roadNetworkObject.SetActive(showGangnamRoadNetwork);
            if (buildingsContainerObject != null) buildingsContainerObject.SetActive(showGangnamBuildings);
            UpdateH3Visibility();
        }

        private void BuildProceduralGangnamBuildings(MapData map)
        {
            buildingsContainerObject = new GameObject("[GangnamBuildings]");
            buildingsContainerObject.transform.SetParent(transform, false);

            List<Vector2> roadSamplePoints = new List<Vector2>();
            foreach (var r in map.roads)
            {
                if (r.points != null)
                {
                    foreach (var p in r.points) roadSamplePoints.Add(new Vector2(p.x, p.z));
                }
            }

            System.Random rand = new System.Random(42);
            Material[] towerMats = { matTowerGlass, matTowerConcrete, matTowerDark };

            for (float bx = -540f; bx <= 540f; bx += 60f)
            {
                for (float bz = -660f; bz <= 660f; bz += 60f)
                {
                    // Check clearance from road network
                    float minDist = float.MaxValue;
                    Vector2 bPos = new Vector2(bx, bz);
                    for (int i = 0; i < roadSamplePoints.Count; i += 2)
                    {
                        float d = Vector2.Distance(bPos, roadSamplePoints[i]);
                        if (d < minDist) minDist = d;
                        if (minDist < 25f) break;
                    }

                    if (minDist < 25f) continue; // Too close to road, leave clear!

                    // Spawn office skyscraper
                    GameObject bldg = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    bldg.name = $"Building_{bx}_{bz}";
                    bldg.transform.SetParent(buildingsContainerObject.transform, false);

                    float distFromCenter = Mathf.Sqrt(bx * bx + bz * bz);
                    float heightBase = Mathf.Lerp(110f, 35f, Mathf.Clamp01(distFromCenter / 700f));
                    float height = heightBase + (float)(rand.NextDouble() * 30.0 - 15.0);
                    float width = 32f + (float)(rand.NextDouble() * 12.0 - 6.0);
                    float depth = 32f + (float)(rand.NextDouble() * 12.0 - 6.0);

                    bldg.transform.localScale = new Vector3(width, height, depth);
                    bldg.transform.position = new Vector3(bx, height * 0.5f, bz);
                    Destroy(bldg.GetComponent<Collider>());

                    Material chosenMat = towerMats[rand.Next(towerMats.Length)];
                    bldg.GetComponent<Renderer>().sharedMaterial = chosenMat;
                }
            }
        }

        private void UpdateH3Visibility()
        {
            foreach (var cellObj in h3CellObjects)
            {
                if (cellObj != null) cellObj.SetActive(showH3Cells);
            }
        }

        private void ApplyCityVisibility()
        {
            if (sampleCityObject == null) FindSampleCityObject();
            if (sampleCityObject != null)
            {
                sampleCityObject.SetActive(showSample3x3City);
            }
        }

        public void SetGangnamOverviewCamera()
        {
            if (cameraView != null)
            {
                cameraView.mapCenter = Vector3.zero;
                cameraView.overviewSize = 850f;
                cameraView.overview = true;
                cameraView.ApplyView();
            }
        }

        public void SetIntersectionCamera()
        {
            if (cameraView != null)
            {
                cameraView.mapCenter = Vector3.zero;
                cameraView.overviewSize = 250f;
                cameraView.overview = true;
                cameraView.ApplyView();
            }
        }

        // ==============================================================
        // 4. Playback & Entity Interpolation
        // ==============================================================
        private void Update()
        {
            if (!isLoaded || replayData == null || replayData.frames.Count == 0) return;

            if (isPlaying)
            {
                currentTime += Time.deltaTime * playbackSpeed;
                if (currentTime >= replayData.duration)
                {
                    currentTime = replayData.duration;
                    isPlaying = false;
                }
                UpdateSimulation(currentTime);
            }
        }

        public void UpdateSimulation(float time)
        {
            if (replayData == null || replayData.frames == null || replayData.frames.Count == 0) return;

            int count = replayData.frames.Count;

            int idxA = 0;
            int idxB = 0;
            float factor = 0f;

            if (time <= replayData.frames[0].t)
            {
                idxA = idxB = 0;
                factor = 0f;
            }
            else if (time >= replayData.frames[count - 1].t)
            {
                idxA = idxB = count - 1;
                factor = 0f;
            }
            else
            {
                int low = 0;
                int high = count - 1;
                while (low <= high)
                {
                    int mid = (low + high) / 2;
                    if (replayData.frames[mid].t <= time)
                    {
                        idxA = mid;
                        low = mid + 1;
                    }
                    else
                    {
                        high = mid - 1;
                    }
                }
                idxB = Mathf.Min(idxA + 1, count - 1);
                float span = replayData.frames[idxB].t - replayData.frames[idxA].t;
                factor = span > 0.0001f ? Mathf.Clamp01((time - replayData.frames[idxA].t) / span) : 0f;
            }

            FrameData fA = replayData.frames[idxA];
            FrameData fB = replayData.frames[idxB];

            Dictionary<int, VehicleState> vLookupB = new Dictionary<int, VehicleState>();
            if (fB.vehicles != null)
            {
                for (int i = 0; i < fB.vehicles.Count; i++)
                {
                    vLookupB[fB.vehicles[i].i] = fB.vehicles[i];
                }
            }

            HashSet<int> activeInCurrentFrame = new HashSet<int>();

            if (fA.vehicles != null)
            {
                for (int i = 0; i < fA.vehicles.Count; i++)
                {
                    VehicleState vA = fA.vehicles[i];
                    activeInCurrentFrame.Add(vA.i);

                    Vector3 posA = new Vector3(vA.x, groundY, vA.z);
                    float rotA = vA.a;

                    Vector3 finalPos = posA;
                    float finalAngle = rotA;
                    int finalState = vA.s;

                    if (vLookupB.TryGetValue(vA.i, out VehicleState vB))
                    {
                        Vector3 posB = new Vector3(vB.x, groundY, vB.z);
                        finalPos = Vector3.Lerp(posA, posB, factor);
                        finalAngle = Mathf.LerpAngle(rotA, vB.a, factor);
                        finalState = factor >= 0.5f ? vB.s : vA.s;
                    }

                    GameObject vObj = GetOrCreateVehicle(vA.i);
                    vObj.transform.position = finalPos;
                    vObj.transform.rotation = Quaternion.Euler(0f, finalAngle, 0f);
                    if (!vObj.activeSelf) vObj.SetActive(true);

                    UpdateStatusMarker(vA.i, finalState);
                }
            }

            foreach (var kvp in vehicleInstances)
            {
                if (!activeInCurrentFrame.Contains(kvp.Key))
                {
                    if (kvp.Value.activeSelf) kvp.Value.SetActive(false);
                }
            }

            UpdatePeople(fA.people);
        }

        private GameObject GetOrCreateVehicle(int entityIndex)
        {
            if (vehicleInstances.TryGetValue(entityIndex, out GameObject existing))
            {
                return existing;
            }

            string kind = "taxi";
            string id = $"veh_{entityIndex}";
            if (replayData != null && replayData.entities != null && entityIndex < replayData.entities.Count)
            {
                kind = replayData.entities[entityIndex].kind ?? "taxi";
                id = replayData.entities[entityIndex].id ?? id;
            }

            GameObject prefabToUse = taxiPrefab;
            if (kind.ToLower().Contains("obs") || kind.ToLower().Contains("kickboard"))
            {
                prefabToUse = obstaclePrefab != null ? obstaclePrefab : civilianCarPrefab;
            }
            else if (!kind.ToLower().Contains("taxi") && !id.ToLower().Contains("taxi"))
            {
                prefabToUse = civilianCarPrefab != null ? civilianCarPrefab : taxiPrefab;
            }

            GameObject instance;
            if (prefabToUse != null)
            {
                instance = Instantiate(prefabToUse, transform);
                foreach (var anim in instance.GetComponentsInChildren<Animator>())
                {
                    anim.enabled = false;
                }
            }
            else
            {
                instance = GameObject.CreatePrimitive(PrimitiveType.Cube);
                instance.transform.SetParent(transform, false);
                instance.transform.localScale = new Vector3(1.8f, 1.4f, 4.2f);
                Destroy(instance.GetComponent<Collider>());
            }

            instance.name = id;

            GameObject marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            marker.name = "StatusMarker";
            marker.transform.SetParent(instance.transform, false);
            marker.transform.localPosition = new Vector3(0f, 2.3f, 0f);
            marker.transform.localScale = new Vector3(0.65f, 0.65f, 0.65f);
            Destroy(marker.GetComponent<Collider>());

            Renderer markerRenderer = marker.GetComponent<Renderer>();
            markerRenderer.sharedMaterial = matIdle;

            vehicleInstances[entityIndex] = instance;
            statusMarkers[entityIndex] = markerRenderer;

            return instance;
        }

        private void UpdateStatusMarker(int entityIndex, int state)
        {
            if (!statusMarkers.TryGetValue(entityIndex, out Renderer renderer)) return;

            if (lastVehicleStates.TryGetValue(entityIndex, out int lastState) && lastState == state)
            {
                return;
            }

            lastVehicleStates[entityIndex] = state;
            switch (state)
            {
                case 0: renderer.sharedMaterial = matIdle; break;
                case 1: renderer.sharedMaterial = matPickup; break;
                case 2: renderer.sharedMaterial = matOccupied; break;
                case 3: renderer.sharedMaterial = matReposition; break;
                case 4: renderer.sharedMaterial = matCivilian; break;
                case 5: renderer.sharedMaterial = matAuto; break;
                case 6: renderer.sharedMaterial = matObstacle; break;
                default: renderer.sharedMaterial = matIdle; break;
            }
        }

        private void UpdatePeople(List<PersonPos> people)
        {
            int targetCount = people != null ? people.Count : 0;

            while (personPool.Count < targetCount)
            {
                GameObject pin = GameObject.CreatePrimitive(PrimitiveType.Capsule);
                pin.name = $"Person_{personPool.Count}";
                pin.transform.SetParent(transform, false);
                pin.transform.localScale = new Vector3(0.6f, 1.0f, 0.6f);
                Destroy(pin.GetComponent<Collider>());
                pin.GetComponent<Renderer>().sharedMaterial = matPerson;
                personPool.Add(pin);
            }

            for (int i = 0; i < targetCount; i++)
            {
                GameObject pin = personPool[i];
                pin.transform.position = new Vector3(people[i].x, groundY + 0.8f, people[i].z);
                if (!pin.activeSelf) pin.SetActive(true);
            }

            for (int i = targetCount; i < personPool.Count; i++)
            {
                if (personPool[i].activeSelf) personPool[i].SetActive(false);
            }
        }

        public void SelectTaxiForCamera(int delta)
        {
            if (activeTaxiIndices.Count == 0) return;

            currentFollowTaxiIndex = (currentFollowTaxiIndex + delta + activeTaxiIndices.Count) % activeTaxiIndices.Count;
            int taxiEntityId = activeTaxiIndices[currentFollowTaxiIndex];

            if (vehicleInstances.TryGetValue(taxiEntityId, out GameObject taxiObj))
            {
                if (cameraView != null)
                {
                    cameraView.target = taxiObj.transform;
                }
            }
        }

        public void SelectFirstTaxiForCamera()
        {
            SelectTaxiForCamera(0);
        }

        private void ClearAllInstances()
        {
            foreach (var kvp in vehicleInstances)
            {
                if (kvp.Value != null) Destroy(kvp.Value);
            }
            vehicleInstances.Clear();
            statusMarkers.Clear();
            lastVehicleStates.Clear();

            foreach (var p in personPool)
            {
                if (p != null) Destroy(p);
            }
            personPool.Clear();
        }

        private FrameData GetCurrentFrameData()
        {
            if (replayData == null || replayData.frames == null || replayData.frames.Count == 0) return null;
            int count = replayData.frames.Count;
            int idx = Mathf.Clamp(Mathf.FloorToInt((currentTime / Mathf.Max(replayData.duration, 1f)) * count), 0, count - 1);
            return replayData.frames[idx];
        }

        // ==============================================================
        // 5. IMGUI Dashboard & Interactive Controls
        // ==============================================================
        private void InitStyles()
        {
            if (isStyleInitialized) return;
            isStyleInitialized = true;

            headerStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 14,
                fontStyle = FontStyle.Bold,
                normal = { textColor = Color.white }
            };

            boldStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 12,
                fontStyle = FontStyle.Bold,
                normal = { textColor = new Color(0.9f, 0.95f, 1.0f) }
            };

            statusStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = 11,
                normal = { textColor = Color.yellow }
            };
        }

        private void OnGUI()
        {
            InitStyles();

            // -------------------------------------------------------------
            // A. Top-Right KPI Dashboard
            // -------------------------------------------------------------
            float dashWidth = 350f;
            float dashHeight = 440f;
            Rect dashRect = new Rect(Screen.width - dashWidth - 18, 18, dashWidth, dashHeight);
            GUILayout.BeginArea(dashRect, GUI.skin.box);

            GUILayout.Label("<b>AI MOBILITY REPLAY DASHBOARD</b>", headerStyle);
            GUILayout.Space(4);

            // Strategy Switcher Buttons
            GUILayout.BeginHorizontal();
            GUI.color = replayFileName == "rl_prepositioned.json" ? new Color(0.3f, 0.9f, 1f) : Color.white;
            if (GUILayout.Button("RL 선제재배치 (AI)"))
            {
                LoadMapAndReplay("rl_prepositioned.json");
            }
            GUI.color = replayFileName == "patrol.json" ? new Color(0.3f, 0.9f, 1f) : Color.white;
            if (GUILayout.Button("일반 순찰 (Baseline)"))
            {
                LoadMapAndReplay("patrol.json");
            }
            GUI.color = Color.white;
            GUILayout.EndHorizontal();

            GUILayout.Space(4);
            GUILayout.Label($"전략: <b>{CurrentStrategyName}</b>", boldStyle);
            GUILayout.Label($"시뮬레이션 시각: <color=#00FFAA><b>{CurrentClockString}</b></color> (진행 {currentTime:F0}s / {replayData?.duration ?? 0:F0}s)", statusStyle);
            GUILayout.Space(4);

            FrameData cur = GetCurrentFrameData();
            if (cur != null)
            {
                float fulfillRate = cur.requested > 0 ? ((float)cur.picked / cur.requested * 100f) : 100f;

                GUILayout.Label($"• 누적 승객 호출: <b>{cur.requested}</b> 건");
                GUILayout.Label($"• 탑승 완료: <b>{cur.picked}</b> 명  (이행률: <color=#66FF66><b>{fulfillRate:F1}%</b></color>)");
                GUILayout.Label($"• 현재 대기 승객: <color=#FFAA00><b>{cur.waiting}</b></color> 명");
                GUILayout.Label($"• 평균 대기 시간: <color=#FFDD44><b>{cur.avgWait:F1}</b></color> 초");
                GUILayout.Label($"• AI 선제 재배치 누적 이동: <color=#00CCFF><b>{cur.moves}</b></color> 건");
                GUILayout.Label($"• 호출 취소/타임아웃: <color=#FF6666><b>{cur.timedOut}</b></color> 명");
            }

            GUILayout.Space(4);
            GUILayout.Label("<b>상태 범례:</b> 🟢대기 | 🟡픽업 | 🔴탑승 | <color=#00CCFF>🔵AI재배치</color>");
            GUILayout.Space(6);

            // View & Map Controls
            GUILayout.Label("<b>🏙️ 강남역 환경 & 시점 제어</b>", boldStyle);
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("강남역 전체 뷰")) SetGangnamOverviewCamera();
            if (GUILayout.Button("사거리 확대 뷰")) SetIntersectionCamera();
            GUILayout.EndHorizontal();

            bool prevRoad = showGangnamRoadNetwork;
            showGangnamRoadNetwork = GUILayout.Toggle(showGangnamRoadNetwork, "실제 강남역 도로망 3D 표시");
            if (prevRoad != showGangnamRoadNetwork && roadNetworkObject != null)
            {
                roadNetworkObject.SetActive(showGangnamRoadNetwork);
            }

            bool prevBldg = showGangnamBuildings;
            showGangnamBuildings = GUILayout.Toggle(showGangnamBuildings, "강남역 3D 빌딩 숲 (오피스 타워)");
            if (prevBldg != showGangnamBuildings && buildingsContainerObject != null)
            {
                buildingsContainerObject.SetActive(showGangnamBuildings);
            }

            bool prevH3 = showH3Cells;
            showH3Cells = GUILayout.Toggle(showH3Cells, "H3 육각형 공간 인덱싱 격자 표시");
            if (prevH3 != showH3Cells)
            {
                UpdateH3Visibility();
            }

            bool prevSampleCity = showSample3x3City;
            showSample3x3City = GUILayout.Toggle(showSample3x3City, "카카오 3x3 샘플 빌딩 켜기");
            if (prevSampleCity != showSample3x3City)
            {
                ApplyCityVisibility();
            }

            GUILayout.EndArea();

            // -------------------------------------------------------------
            // B. Bottom Playback Control Bar
            // -------------------------------------------------------------
            float barWidth = Screen.width - 36;
            float barHeight = 70f;
            Rect barRect = new Rect(18, Screen.height - barHeight - 16, barWidth, barHeight);
            GUILayout.BeginArea(barRect, GUI.skin.box);

            GUILayout.BeginHorizontal();

            // Play / Pause
            GUI.color = isPlaying ? new Color(1f, 0.8f, 0.4f) : new Color(0.4f, 1f, 0.5f);
            if (GUILayout.Button(isPlaying ? "❚❚ 일시정지" : "▶ 재생", GUILayout.Width(90), GUILayout.Height(26)))
            {
                isPlaying = !isPlaying;
            }
            GUI.color = Color.white;

            // Speed Controls
            GUILayout.Label("  배속:", GUILayout.Width(38));
            float[] speeds = { 1f, 2f, 5f, 10f, 25f, 50f };
            foreach (var s in speeds)
            {
                GUI.color = Mathf.Approximately(playbackSpeed, s) ? new Color(0.3f, 0.9f, 1f) : Color.white;
                if (GUILayout.Button($"{s}x", GUILayout.Width(42), GUILayout.Height(24)))
                {
                    playbackSpeed = s;
                }
            }
            GUI.color = Color.white;

            // Follow Taxi Selector
            GUILayout.FlexibleSpace();
            if (GUILayout.Button("◀ 이전 택시", GUILayout.Width(85), GUILayout.Height(24)))
            {
                SelectTaxiForCamera(-1);
            }
            string taxiLabel = activeTaxiIndices.Count > 0 ? $"택시 #{currentFollowTaxiIndex + 1}/{activeTaxiIndices.Count}" : "택시 없음";
            GUILayout.Label(taxiLabel, GUILayout.Width(90));
            if (GUILayout.Button("다음 택시 ▶", GUILayout.Width(85), GUILayout.Height(24)))
            {
                SelectTaxiForCamera(1);
            }

            GUILayout.EndHorizontal();

            // Timeline Scrubbing Slider
            GUILayout.Space(2);
            GUILayout.BeginHorizontal();
            GUILayout.Label("타임라인", GUILayout.Width(60));
            float maxDuration = replayData != null && replayData.duration > 0 ? replayData.duration : 100f;
            float newTime = GUILayout.HorizontalSlider(currentTime, 0f, maxDuration);
            if (Mathf.Abs(newTime - currentTime) > 0.05f)
            {
                currentTime = newTime;
                UpdateSimulation(currentTime);
            }
            GUILayout.Label($"{currentTime:F0}s / {maxDuration:F0}s", GUILayout.Width(110));
            GUILayout.EndHorizontal();

            GUILayout.EndArea();
        }

        private void OnDestroy()
        {
            ClearAllInstances();
        }
    }
}
'''

target_path = r'J:\chromdowload\zip\Fetex-Unity-main\Fetex-Unity-main\Assets\Fetex\Scripts\FetexReplayPlayer.cs'
with open(target_path, 'w', encoding='utf-8') as f:
    f.write(code)
print('Successfully written FetexReplayPlayer.cs with Buildings and Double-sided junctions')

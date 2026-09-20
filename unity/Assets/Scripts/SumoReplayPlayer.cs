using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// export_unity_replay.py가 만든 patrol.json/forecast.json을 재생한다.
/// 제공 Asset prefab은 Inspector에서 연결하고, 비어 있으면 primitive fallback을 쓴다.
/// </summary>
public sealed class SumoReplayPlayer : MonoBehaviour
{
    [Header("Replay input")]
    public TextAsset replayJson;
    public bool playOnStart = true;
    public bool loop = true;
    [Min(0.01f)] public float simulationSecondsPerRealSecond = 30f;
    public Vector3 worldOffset;

    [Header("Provided Asset prefabs")]
    public GameObject taxiPrefab;
    public GameObject normalVehiclePrefab;
    public GameObject autonomousVehiclePrefab;
    public GameObject obstaclePrefab;

    private Replay replay;
    private readonly Dictionary<int, GameObject> instances = new Dictionary<int, GameObject>();
    private float startedAt;
    private bool isPlaying;

    [Serializable] private class Replay
    {
        public float duration;
        public Entity[] entities;
        public Frame[] frames;
    }

    [Serializable] private class Entity { public string id; public string kind; }
    [Serializable] private class Frame { public float t; public Vehicle[] vehicles; }
    [Serializable] private class Vehicle { public int i; public float x; public float z; public float a; public int s; }

    private void Start()
    {
        if (replayJson == null)
        {
            Debug.LogError("SumoReplayPlayer: Replay Json을 지정하세요.");
            enabled = false;
            return;
        }
        replay = JsonUtility.FromJson<Replay>(replayJson.text);
        if (replay == null || replay.frames == null || replay.frames.Length == 0)
        {
            Debug.LogError("SumoReplayPlayer: replay JSON에 frames가 없습니다.");
            enabled = false;
            return;
        }
        ApplyFrame(replay.frames[0]);
        startedAt = Time.time;
        isPlaying = playOnStart;
    }

    private void Update()
    {
        if (!isPlaying) return;
        var simulatedTime = (Time.time - startedAt) * simulationSecondsPerRealSecond;
        if (simulatedTime >= replay.duration)
        {
            if (!loop) { ApplyFrame(replay.frames[replay.frames.Length - 1]); isPlaying = false; return; }
            startedAt = Time.time;
            simulatedTime = 0f;
        }
        ApplyFrame(FrameAt(simulatedTime));
    }

    public void Play() { startedAt = Time.time; isPlaying = true; }
    public void Pause() { isPlaying = false; }

    private Frame FrameAt(float simulatedTime)
    {
        var selected = replay.frames[0];
        foreach (var frame in replay.frames)
        {
            if (frame.t > simulatedTime) break;
            selected = frame;
        }
        return selected;
    }

    private void ApplyFrame(Frame frame)
    {
        foreach (var instance in instances.Values) instance.SetActive(false);
        if (frame.vehicles == null) return;
        foreach (var vehicle in frame.vehicles)
        {
            var instance = InstanceFor(vehicle.i);
            instance.SetActive(true);
            instance.transform.SetPositionAndRotation(
                worldOffset + new Vector3(vehicle.x, 0f, vehicle.z),
                Quaternion.Euler(0f, vehicle.a, 0f));
        }
    }

    private GameObject InstanceFor(int entityIndex)
    {
        if (instances.TryGetValue(entityIndex, out var existing)) return existing;
        var entity = replay.entities != null && entityIndex < replay.entities.Length ? replay.entities[entityIndex] : null;
        var prefab = PrefabFor(entity == null ? string.Empty : entity.kind);
        var instance = prefab != null ? Instantiate(prefab, transform) : CreateFallback(entity == null ? "vehicle" : entity.kind);
        instance.name = entity == null ? $"entity_{entityIndex}" : entity.id;
        instances.Add(entityIndex, instance);
        return instance;
    }

    private GameObject PrefabFor(string kind)
    {
        kind = kind ?? string.Empty;
        if (kind == "taxi") return taxiPrefab;
        if (kind.Contains("auto")) return autonomousVehiclePrefab;
        if (kind.Contains("obstacle")) return obstaclePrefab;
        return normalVehiclePrefab;
    }

    private GameObject CreateFallback(string kind)
    {
        var primitive = kind.Contains("obstacle") ? PrimitiveType.Cube : PrimitiveType.Capsule;
        var fallback = GameObject.CreatePrimitive(primitive);
        fallback.transform.SetParent(transform, false);
        fallback.transform.localScale = kind == "taxi" ? new Vector3(1.2f, 0.6f, 2.4f) : new Vector3(1f, 0.5f, 2f);
        return fallback;
    }
}

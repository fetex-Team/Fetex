using System;
using System.Collections.Generic;

namespace Fetex
{
    // ==============================================================
    // 차선 경로 기반 차량 위치 복원 (재생 포맷 v2)
    //
    // 예전에는 10초 간격 두 점을 직선으로 이어서, 교차로에서 좌·우회전할 때
    // 차가 모서리를 가로질러 도로 밖으로 나갔다. 이제는 두 프레임 사이에 차가
    // 실제로 지나간 차선들(교차로 안쪽 연결 차선 포함)의 중심선을 따라 움직인다.
    //
    // UnityEngine에 의존하지 않는 순수 C# 코드라 유니티 없이도 컴파일·테스트할 수 있다.
    // 파이썬 판 tools/replay_motion.py와 같은 규칙이며, tests/test_replay_motion.py가
    // 두 구현의 결과가 같은지 확인한다. 한쪽을 고치면 다른 쪽도 같이 고칠 것.
    // ==============================================================

    /// <summary>map.json의 lanes 한 개 (SUMO 차선 중심선).</summary>
    public sealed class MotionLane
    {
        public readonly string Id;
        public readonly int EdgeIndex;
        public readonly float Length;   // SUMO 길이 (lanePos 단위)
        public readonly float Width;
        readonly float[] xs;
        readonly float[] zs;
        readonly float[] cum;           // 꼭짓점까지 누적 기하 길이

        public MotionLane(string id, int edgeIndex, float length, float width, IList<float> flatXZ)
        {
            Id = id;
            EdgeIndex = edgeIndex;
            Length = length;
            Width = width;
            int n = flatXZ != null ? flatXZ.Count / 2 : 0;
            if (n == 0)
            {
                xs = new float[] { 0f };
                zs = new float[] { 0f };
                cum = new float[] { 0f };
                return;
            }
            xs = new float[n];
            zs = new float[n];
            cum = new float[n];
            for (int i = 0; i < n; i++)
            {
                xs[i] = flatXZ[2 * i];
                zs[i] = flatXZ[2 * i + 1];
                if (i > 0)
                {
                    float dx = xs[i] - xs[i - 1];
                    float dz = zs[i] - zs[i - 1];
                    cum[i] = cum[i - 1] + (float)Math.Sqrt(dx * dx + dz * dz);
                }
            }
        }

        public float GeomLength { get { return cum[cum.Length - 1]; } }

        /// <summary>SUMO 차선 위치 s(m) → (x, z). 범위 밖이면 양 끝으로 고정.</summary>
        public void PointAt(float s, out float x, out float z)
        {
            if (xs.Length == 1)
            {
                x = xs[0];
                z = zs[0];
                return;
            }
            float g = GeomLength;
            float sg = Length > 1e-6f ? s * (g / Length) : 0f;
            if (sg < 0f) sg = 0f;
            if (sg > g) sg = g;
            int lo = 0, hi = cum.Length - 1;
            while (hi - lo > 1)
            {
                int mid = (lo + hi) / 2;
                if (cum[mid] <= sg) lo = mid; else hi = mid;
            }
            float seg = cum[hi] - cum[lo];
            float u = seg > 1e-9f ? (sg - cum[lo]) / seg : 0f;
            x = xs[lo] + (xs[hi] - xs[lo]) * u;
            z = zs[lo] + (zs[hi] - zs[lo]) * u;
        }
    }

    /// <summary>한 프레임의 차량 상태 중 위치 복원에 필요한 값.</summary>
    public struct MotionState
    {
        public int Lane;      // 차선 번호 (-1: 모름/텔레포트)
        public float Pos;     // 차선 위 앞범퍼 위치 (m)
        public float Speed;   // m/s
        public float X, Z;    // 차 중심 좌표 (직선 보간 대체용)
        public float Angle;   // 도 (북쪽 0, 시계방향)
        public int[] Via;     // 이전 프레임 이후 지나온 차선들 (B 프레임 쪽 값)
    }

    public static class ReplayMotion
    {
        public const float HeadingProbe = 1.5f;
        public const float TeleportSlack = 30f;
        public const float MaxEndpointOffset = 8f;
        const int MaxSegments = 64;

        struct Segment
        {
            public int LaneFrom, LaneTo;
            public float S0, S1;
            public float Len { get { return S1 > S0 ? S1 - S0 : 0f; } }
        }

        // 메인 스레드 전용 작업 버퍼 (프레임마다 할당하지 않기 위해)
        static readonly Segment[] segs = new Segment[MaxSegments];
        static readonly int[] seqBuf = new int[MaxSegments + 2];

        /// <summary>Unity Mathf.LerpAngle과 같은 최단 방향 각도 보간.</summary>
        public static float LerpAngle(float a, float b, float t)
        {
            float delta = (b - a) % 360f;
            if (delta < 0f) delta += 360f;
            if (delta > 180f) delta -= 360f;
            return a + delta * t;
        }

        public static void Linear(in MotionState a, in MotionState b, float f, out float x, out float z, out float heading)
        {
            x = a.X + (b.X - a.X) * f;
            z = a.Z + (b.Z - a.Z) * f;
            heading = LerpAngle(a.Angle, b.Angle, f);
        }

        static void SegPoint(IList<MotionLane> lanes, in Segment sg, float s, out float x, out float z)
        {
            MotionLane from = lanes[sg.LaneFrom];
            from.PointAt(s, out x, out z);
            if (sg.LaneTo == sg.LaneFrom) return;
            float span = sg.S1 - sg.S0;
            float t = span > 1e-6f ? (s - sg.S0) / span : 1f;
            if (t < 0f) t = 0f;
            if (t > 1f) t = 1f;
            t = t * t * (3f - 2f * t);
            MotionLane to = lanes[sg.LaneTo];
            float ratio = from.Length > 1e-6f ? to.Length / from.Length : 1f;
            to.PointAt(s * ratio, out float bx, out float bz);
            x += (bx - x) * t;
            z += (bz - z) * t;
        }

        static void PathPoint(IList<MotionLane> lanes, int count, float c, out float x, out float z)
        {
            if (c <= 0f)
            {
                float s = segs[0].S0 + c;
                lanes[segs[0].LaneFrom].PointAt(s > 0f ? s : 0f, out x, out z);
                return;
            }
            float acc = 0f;
            for (int i = 0; i < count; i++)
            {
                float len = segs[i].Len;
                if (c <= acc + len || i == count - 1)
                {
                    float d = c - acc;
                    if (d > len) d = len;
                    SegPoint(lanes, segs[i], segs[i].S0 + d, out x, out z);
                    return;
                }
                acc += len;
            }
            SegPoint(lanes, segs[count - 1], segs[count - 1].S1, out x, out z);
        }

        /// <summary>경로 구간을 만든다. 실패하면 -1, 성공하면 구간 수.</summary>
        static int BuildPath(IList<MotionLane> lanes, in MotionState a, in MotionState b)
        {
            int n = lanes.Count;
            if (a.Lane < 0 || b.Lane < 0 || a.Lane >= n || b.Lane >= n) return -1;
            int viaCount = b.Via != null ? b.Via.Length : 0;
            if (viaCount > MaxSegments) return -1;
            int m = 0;
            seqBuf[m++] = a.Lane;
            for (int i = 0; i < viaCount; i++)
            {
                int w = b.Via[i];
                if (w < 0 || w >= n) return -1;
                if (w != seqBuf[m - 1]) seqBuf[m++] = w;
            }
            if (b.Lane != seqBuf[m - 1]) seqBuf[m++] = b.Lane;

            // 같은 도로(edge)의 연속 차선은 "차선 변경" 한 구간으로 묶는다
            int k = 0;
            for (int i = 0; i < m; i++)
            {
                if (k > 0 && lanes[segs[k - 1].LaneTo].EdgeIndex == lanes[seqBuf[i]].EdgeIndex)
                {
                    segs[k - 1].LaneTo = seqBuf[i];
                    continue;
                }
                if (k >= MaxSegments) return -1;
                segs[k].LaneFrom = seqBuf[i];
                segs[k].LaneTo = seqBuf[i];
                k++;
            }
            for (int i = 0; i < k; i++)
            {
                segs[i].S0 = i == 0 ? a.Pos : 0f;
                segs[i].S1 = i == k - 1 ? b.Pos : lanes[segs[i].LaneFrom].Length;
            }
            if (k == 1 && segs[0].S1 < segs[0].S0)
            {
                if (segs[0].S0 - segs[0].S1 > 0.5f) return -1;  // 뒤로 감 → 텔레포트/재경로
                segs[0].S1 = segs[0].S0;
            }
            return k;
        }

        static float HermiteDistance(float f, float D, float v0, float v1, float dt)
        {
            if (D <= 1e-6f) return 0f;
            float m0 = Clamp(v0 * dt, 0f, 3f * D);
            float m1 = Clamp(v1 * dt, 0f, 3f * D);
            float f2 = f * f, f3 = f2 * f;
            return (f3 - 2f * f2 + f) * m0 + (-2f * f3 + 3f * f2) * D + (f3 - f2) * m1;
        }

        static float Clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

        /// <summary>
        /// 두 프레임 사이 진행률 f(0~1)에서 차 중심 위치와 방향을 구한다.
        /// 차선 경로를 쓸 수 없으면 직선 보간으로 대체하고 false를 돌려준다.
        /// </summary>
        public static bool Interpolate(IList<MotionLane> lanes, in MotionState a, in MotionState b, float f, float dt,
                                       float vehicleLength, out float x, out float z, out float heading)
        {
            int k = lanes != null ? BuildPath(lanes, a, b) : -1;
            if (k <= 0)
            {
                Linear(a, b, f, out x, out z, out heading);
                return false;
            }
            float D = 0f;
            for (int i = 0; i < k; i++) D += segs[i].Len;
            float vmax = Math.Max(Math.Max(a.Speed, b.Speed), 1f);
            if (D > vmax * dt * 2f + TeleportSlack)
            {
                Linear(a, b, f, out x, out z, out heading);
                return false;
            }
            float d = HermiteDistance(f, D, a.Speed, b.Speed, dt);
            float half = vehicleLength * 0.5f;
            float c = d - half;
            PathPoint(lanes, k, c, out x, out z);

            // 끝점 보정: 기록된 차 중심(갓길 정차로 옆에 비켜선 위치 등)과 경로상 위치의 차이를
            // 양 끝에서 0이 되도록 선형으로 나눠 더한다 → 프레임 시점에는 기록 위치와 정확히 일치.
            PathPoint(lanes, k, -half, out float ax0, out float az0);
            PathPoint(lanes, k, D - half, out float bx1, out float bz1);
            float oax = a.X - ax0, oaz = a.Z - az0;
            float obx = b.X - bx1, obz = b.Z - bz1;
            if (oax * oax + oaz * oaz > MaxEndpointOffset * MaxEndpointOffset) { oax = 0f; oaz = 0f; }
            if (obx * obx + obz * obz > MaxEndpointOffset * MaxEndpointOffset) { obx = 0f; obz = 0f; }
            x += oax * (1f - f) + obx * f;
            z += oaz * (1f - f) + obz * f;
            PathPoint(lanes, k, c - HeadingProbe, out float bx, out float bz);
            PathPoint(lanes, k, c + HeadingProbe, out float fx, out float fz);
            float hx = fx - bx, hz = fz - bz;
            if (hx * hx + hz * hz < 0.0025f)
            {
                heading = LerpAngle(a.Angle, b.Angle, f);
            }
            else
            {
                heading = (float)(Math.Atan2(hx, hz) * 180.0 / Math.PI);
                if (heading < 0f) heading += 360f;
            }
            return true;
        }
    }
}

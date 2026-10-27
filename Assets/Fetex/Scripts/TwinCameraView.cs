using UnityEngine;
using Fetex;

// 같은 카메라를 운영 탑뷰와 선택 차량 후방 시점 사이에서 전환한다.
[RequireComponent(typeof(Camera))]
public sealed class TwinCameraView : MonoBehaviour
{
    public Transform target;
    public Vector3 mapCenter = new Vector3(0, 0, -0.559f);
    public float overviewSize = 225f;
    public Vector3 followOffset = new Vector3(0, 3.5f, -9f);
    public bool overview = true;
    Camera view;

    void Awake() { view = GetComponent<Camera>(); }

    void LateUpdate()
    {
        ApplyView();
    }

    public void ApplyView()
    {
        if (view == null) view = GetComponent<Camera>();
        view.orthographic = overview;
        if (overview)
        {
            view.orthographicSize = overviewSize;
            transform.SetPositionAndRotation(mapCenter + Vector3.up * 500f, Quaternion.Euler(90, 0, 0));
        }
        else if (target != null)
        {
            transform.position = target.position + target.rotation * followOffset;
            transform.LookAt(target.position + target.forward * 5f + Vector3.up * 1.1f);
        }
    }

    void OnGUI()
    {
        // IMGUI 버튼 포커스 이동보다 먼저 Tab 전환을 처리한다.
        if (Event.current.type == EventType.KeyDown && Event.current.keyCode == KeyCode.Tab)
        {
            overview = !overview;
            Event.current.Use();
        }
        GUILayout.BeginArea(new Rect(18, 18, 290, 115), GUI.skin.box);
        GUILayout.Label("<b>FETEX / KAKAO DIGITAL TWIN</b>");
        if (FetexReplayPlayer.Instance != null && FetexReplayPlayer.Instance.isLoaded)
        {
            GUILayout.Label("<color=#55FF55>● 리플레이 연동 활성화됨</color>");
        }
        else
        {
            GUILayout.Label("Scene preview - simulation not connected");
        }
        GUILayout.BeginHorizontal();
        if (GUILayout.Button("Overview (탑뷰)")) overview = true;
        if (GUILayout.Button("Follow (추적뷰)"))
        {
            overview = false;
            if (target == null && FetexReplayPlayer.Instance != null)
            {
                FetexReplayPlayer.Instance.SelectFirstTaxiForCamera();
            }
        }
        GUILayout.EndHorizontal();
        GUILayout.Label("단축키 [TAB]: 시점 즉시 전환");
        GUILayout.EndArea();
    }
}

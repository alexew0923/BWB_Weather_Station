using UnityEngine;
using UnityEngine.InputSystem;

public class CameraMovement : MonoBehaviour
{
    private float targetZoom;
    private float velocity = 0f;
    private float smoothTime = 0.25f;

    private Vector3 targetPosition;
    private Vector3 positionVelocity = Vector3.zero;

    public GameObject prefab;
    public GameObject back;
    public GameObject plantPanel;
    public GameObject infoPanel;
    public GameObject info;
    private Camera cam;

    public int id;
    public RaycastHit2D rayHit;

    public Vector2 position;

    private void Awake()
    {
        cam = Camera.main;
        targetZoom = cam.orthographicSize;
        targetPosition = cam.transform.position;
    }

    public void Position (InputAction.CallbackContext context) {
        position = context.ReadValue<Vector2>();
    }

    public void OnClick(InputAction.CallbackContext context)
    {
        if (context.canceled) {
            var rayHit = Physics2D.GetRayIntersection(cam.ScreenPointToRay(position));
            if (rayHit.collider == null) return;
            if (rayHit.collider.gameObject.name == "MedicineGarden" && !back.activeSelf) {
                info.SetActive(false);
                back.SetActive(true);
                // Example: Zoom in and center on (x, y) = (2, 3)
                Vector3 focusPoint = new Vector3(-2f, -5f, cam.transform.position.z);
                targetPosition = focusPoint;
                targetZoom = 5f;
                for (int i = 0; i < 10; i++) {
                    GameObject clone = Instantiate(prefab);
                    RaisedBeds raisedBed = clone.GetComponent<RaisedBeds>();
                    raisedBed.InitializePrefab(i);
                }
            }
            if (rayHit.collider.tag == "Clone" && !plantPanel.activeSelf) {
                id = rayHit.collider.gameObject.GetComponent<RaisedBeds>().plantId;
                back.SetActive(false);
                plantPanel.SetActive(true);
            }
        }
    }

    void Update()
    {
        rayHit = Physics2D.GetRayIntersection(cam.ScreenPointToRay(position));

        // Smoothly interpolate the camera's orthographic size to the target zoom
        cam.orthographicSize = Mathf.SmoothDamp(cam.orthographicSize, targetZoom, ref velocity, smoothTime);

        // Smoothly interpolate the camera's position to the target position
        cam.transform.position = Vector3.SmoothDamp(cam.transform.position, targetPosition, ref positionVelocity, smoothTime);

        RaisedBeds[] allBeds = Object.FindObjectsByType<RaisedBeds>(FindObjectsSortMode.None);
        foreach (var bed in allBeds)
        {
            bool isHovered = (rayHit.collider != null && rayHit.collider.gameObject == bed.gameObject);
            bed.OnHover(isHovered);
        }
    }

    public void Previous()
    {
        Vector3 focusPoint = new Vector3(0f, 0f, cam.transform.position.z);
        targetPosition = focusPoint;
        targetZoom = 11f;
        back.SetActive(false);
        info.SetActive(true);
        GameObject[] taggedObjects = GameObject.FindGameObjectsWithTag("Clone");
        foreach (GameObject obj in taggedObjects) {
            Destroy(obj);
        }
    }

    public void EnableInfoPanel()
    {
        info.SetActive(false);
        infoPanel.SetActive(true);
    }

    public void DisableInfoPanel()
    {
        infoPanel.SetActive(false);
        info.SetActive(true);
    }

    public void DisablePlantPanel()
    {
        plantPanel.SetActive(false);
        back.SetActive(true);
    }
}
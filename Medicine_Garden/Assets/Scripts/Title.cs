using UnityEngine;
using TMPro;

public class Title : MonoBehaviour
{
    public TMP_Text displayText; // Reference to your TextMeshPro component
    public string[] titles;
    CameraMovement cameraScript;
    public GameObject inputHandler;

    void OnEnable()
    {
        cameraScript = inputHandler.GetComponent<CameraMovement>();
        displayText.text = titles[cameraScript.id];
    }
}
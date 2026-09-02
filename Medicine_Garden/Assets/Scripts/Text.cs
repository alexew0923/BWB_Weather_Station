using UnityEngine;
using TMPro;
using UnityEngine.InputSystem;

public class Text : MonoBehaviour
{
    public TMP_Text displayText; // Reference to your TextMeshPro component
    private CameraMovement cameraScript;
    public GameObject inputHandler;

    void Start()
    {
        cameraScript = inputHandler.GetComponent<CameraMovement>();
    }

    void Update()
    {
        // Make text follow mouse using new Input System
        displayText.transform.position = cameraScript.position + new Vector2(0, 30);
        
        if (cameraScript.rayHit.collider != null && cameraScript.rayHit.collider.gameObject.tag == "Clone") {
            // Display the plant name
            displayText.text = cameraScript.rayHit.collider.gameObject.name;
        } else {
            displayText.text = "";
        }
    }
}
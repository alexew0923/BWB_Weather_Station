using UnityEngine;
using UnityEngine.UI;

public class Image : MonoBehaviour
{
    [SerializeField] Sprite[] plantImages;
    CameraMovement cameraScript;
    public GameObject inputHandler;

    void OnEnable()
    {
        cameraScript = inputHandler.GetComponent<CameraMovement>();
        gameObject.GetComponent<UnityEngine.UI.Image>().sprite = plantImages[cameraScript.id];
    }
}
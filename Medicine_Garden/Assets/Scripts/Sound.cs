using UnityEngine;

public class Sound : MonoBehaviour
{
    [SerializeField] AudioSource musicSource;
    CameraMovement cameraScript;

    public GameObject inputHandler;
    public AudioClip[] pronounciation;

    void Start()
    {
        cameraScript = inputHandler.GetComponent<CameraMovement>();
    }

    public void Play()
    {
        musicSource.clip = pronounciation[cameraScript.id];
        musicSource.Play();        
    }
}
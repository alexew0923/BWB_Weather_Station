using UnityEngine;

public class RaisedBeds : MonoBehaviour
{
    private SpriteRenderer sr;

    public int plantId;
    public Sprite[] shapes;
    public PolygonCollider2D polygonCollider;
    public CircleCollider2D circleCollider;

    float[] xValues = {-2.54f, -0.68f, 0.55f, 0.33f, -1.7f, -3.59f, -4.85f, -4.37f, -4.76f, -1.25f};
    float[] yValues = {-2.29f, -2.68f, -4.52f, -6.42f, -7.79f, -7.43f, -5.32f, -3.46f, -9.1f, -9.69f};
    float[] rValues = {9.5f, -33.4f, -81.8f, -123.8f, -166.7f, -214.7f, -259.3f, -301.9f, 0f, 0f};
    string[] plantNames = {"Sweetgrass", "Sweetgrass", "Tobacco", "Tobacco", "Juniper", "Juniper", "Sage", "Sage", "Cedar", "Cedar"};

    void Awake()
    {
        sr = GetComponent<SpriteRenderer>();
    }

    public void OnHover(bool isHovering)
    {
        if (sr != null)
        {
            Color c = sr.color;
            c.a = isHovering ? 0.9f : 0.7f;
            sr.color = c;
        }
    }

    public void InitializePrefab(int index) {
        plantId = index;
        if (plantId < 8) {
            sr.sprite = shapes[0];
            polygonCollider.enabled = true;
            circleCollider.enabled = false;
        } else {
            sr.sprite = shapes[1];
            circleCollider.enabled = true;
            polygonCollider.enabled = false;
        }
        transform.position = new Vector2(xValues[plantId], yValues[plantId]);
        transform.rotation = Quaternion.Euler(0f, 0f, rValues[plantId]);
        gameObject.tag = "Clone";
        gameObject.name = plantNames[plantId];
    }
}
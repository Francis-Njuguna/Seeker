# Face test fixtures

Four portrait photographs used by `tests/test_face_integration.py` to exercise the
real dlib recognition pipeline. Without real faces those tests could only assert
arithmetic on hand-built vectors, which does not prove the encoder works.

| File | Contents | Used for |
| --- | --- | --- |
| `obama.jpg` | one face | single-face detection, reference embedding |
| `obama2.jpg` | the same person, a different photograph | same-person match |
| `biden.jpg` | a different person, one face | different-person rejection |
| `two_people.jpg` | two faces | multi-face rejection |

## Provenance

These are the example images published by `face_recognition` itself, the library
this project uses, at <https://github.com/ageitgey/face_recognition/tree/master/examples>
(MIT licence). They are official public-domain US government portraits of public
figures.

Reusing the upstream library's own fixtures is deliberate: they are the images its
documented accuracy figures are quoted against, so a distance measured here can be
compared with upstream's. The alternative — photographs of real users — would put
biometric data belonging to identifiable private individuals into version control,
which the privacy rules in `app/face.py` exist to prevent.

## Regenerating

    cd backend/tests/fixtures/faces
    B=https://raw.githubusercontent.com/ageitgey/face_recognition/master/examples
    for f in obama.jpg obama2.jpg biden.jpg two_people.jpg; do curl -sSLO "$B/$f"; done

The integration tests skip with an actionable message if these files are missing,
rather than failing, so a checkout without them still runs the rest of the suite.

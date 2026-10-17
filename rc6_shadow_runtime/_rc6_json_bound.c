#define PY_SSIZE_T_CLEAN
#include <Python.h>
/* Sizing only. This module never encodes values or authorizes their content. */
static int ascii_bound(PyObject *text, Py_ssize_t remaining, Py_ssize_t *result) {
    if (remaining < 2) return 0;
    #if PY_VERSION_HEX < 0x030C0000
    if (PyUnicode_READY(text) < 0) return -1;
    #endif
    Py_ssize_t length=PyUnicode_GET_LENGTH(text), used=2;
    int kind=PyUnicode_KIND(text);
    void *data=PyUnicode_DATA(text);
    for (Py_ssize_t i=0;i<length;i++) {
        Py_UCS4 ch=PyUnicode_READ(kind,data,i);
        Py_ssize_t add;
        if (ch=='"' || ch=='\\' || ch=='\b' || ch=='\f' || ch=='\n' || ch=='\r' || ch=='\t') add=2;
        else if (ch<0x20 || ch>0x7e) add=(ch>0xffff)?12:6;
        else add=1;
        if (add>remaining-used) return 0;
        used+=add;
    }
    *result=used;
    return 1;
}
static PyObject *is_small(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *value,*frozen_type,*pending=NULL,*item=NULL,*hint=NULL;
    Py_ssize_t maximum=65536,nodes=4096;
    if (!PyArg_ParseTuple(args,"OO|nn:is_small",&value,&frozen_type,&maximum,&nodes)) return NULL;
    if (!PyType_Check(frozen_type)) {
        PyErr_SetString(PyExc_TypeError,"FROZEN_JSON_TYPE_REQUIRED");
        return NULL;
    }
    pending=PyList_New(1);
    if (!pending) return NULL;
    Py_INCREF(value);PyList_SET_ITEM(pending,0,value);
    while (PyList_GET_SIZE(pending)) {
        Py_ssize_t last=PyList_GET_SIZE(pending)-1;
        item=PyList_GET_ITEM(pending,last);Py_INCREF(item);
        if (PyList_SetSlice(pending,last,last+1,NULL)<0) goto error;
        if (nodes<=0) goto no;
        nodes--;
        if (Py_TYPE(item)==(PyTypeObject *)frozen_type) {
            hint=PyObject_GetAttrString(item,"_canonical_json");
            if (!hint) goto error;
            Py_ssize_t n=PyObject_Length(hint);
            Py_CLEAR(hint);
            if (n<0) goto error;
            if (n>maximum) goto no;
            maximum-=n;
        } else if (PyUnicode_CheckExact(item)) {
            Py_ssize_t n;
            int fit=ascii_bound(item,maximum,&n);
            if (fit<0) goto error;
            if (!fit) goto no;
            maximum-=n;
        } else if (item==Py_None || PyBool_Check(item)) {
            if (maximum<5) goto no;
            maximum-=5;
        } else if (PyLong_CheckExact(item)) {
            size_t bits=_PyLong_NumBits(item);
            if (bits==(size_t)-1 && PyErr_Occurred()) goto error;
            if (bits<1) bits=1;
            if (maximum<3 || bits>(size_t)(maximum-3)) goto no;
            maximum-=(Py_ssize_t)bits+3;
        } else if (PyFloat_CheckExact(item)) {
            if (maximum<64) goto no;
            maximum-=64;
        } else if (PyDict_CheckExact(item)) {
            Py_ssize_t length=PyDict_Size(item),pos=0;
            if (length>256 || maximum<2+2*length) goto no;
            maximum-=2+2*length;
            PyObject *key,*child;
            while (PyDict_Next(item,&pos,&key,&child)) {
                if (PyUnicode_CheckExact(key)) {
                    Py_ssize_t n;int fit=ascii_bound(key,maximum,&n);
                    if (fit<0) goto error;
                    if (!fit) goto no;
                    maximum-=n;
                } else {
                    if (PyList_Append(pending,key)<0) goto error;
                    if (maximum<2) goto no;
                    maximum-=2;
                }
            }
            pos=0;
            while (PyDict_Next(item,&pos,&key,&child))
                if (PyList_Append(pending,child)<0) goto error;
        } else if (PyList_CheckExact(item) || PyTuple_CheckExact(item)) {
            int tuple=PyTuple_CheckExact(item);
            Py_ssize_t length=tuple?PyTuple_GET_SIZE(item):PyList_GET_SIZE(item);
            if (length>256 || maximum<2+length) goto no;
            maximum-=2+length;
            for (Py_ssize_t i=0;i<length;i++) {
                PyObject *child=tuple?PyTuple_GET_ITEM(item,i):PyList_GET_ITEM(item,i);
                if (PyList_Append(pending,child)<0) goto error;
            }
        } else goto no;
        Py_CLEAR(item);
    }
    Py_DECREF(pending);
    Py_RETURN_TRUE;
no:
    Py_XDECREF(hint);Py_XDECREF(item);Py_DECREF(pending);
    Py_RETURN_FALSE;
error:
    Py_XDECREF(hint);Py_XDECREF(item);Py_XDECREF(pending);
    return NULL;
}
static PyMethodDef methods[]={
    {"is_small",is_small,METH_VARARGS,"Bound canonical ASCII JSON tree size without encoding."},
    {NULL,NULL,0,NULL}
};
static struct PyModuleDef module={PyModuleDef_HEAD_INIT,"_rc6_json_bound",NULL,-1,methods,NULL,NULL,NULL,NULL};
PyMODINIT_FUNC PyInit__rc6_json_bound(void) { return PyModule_Create(&module); }

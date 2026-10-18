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

/* Durable audit input codec; does not change the sizing predicate above. */
#include <math.h>
#include <stdint.h>
#include <string.h>

#define PIECE_MAX 131072
#define MEMO_MAX 4096
#define MEMO_BYTES 262144
typedef struct { char data[PIECE_MAX]; Py_ssize_t used; } Buffer;
typedef struct { PyObject *enc, *dec, *frozen_dict, *frozen_list; Py_ssize_t enc_bytes, dec_bytes; int bad; Buffer *buffer; } Context;
static const char *context_name = "rc6.audit-snapshot.context.v1";

static void context_free(PyObject *capsule) {
    Context *ctx = PyCapsule_GetPointer(capsule, context_name);
    if (!ctx) { PyErr_Clear(); return; }
    Py_XDECREF(ctx->enc); Py_XDECREF(ctx->dec); Py_XDECREF(ctx->frozen_dict);
    Py_XDECREF(ctx->frozen_list); PyMem_Free(ctx->buffer); PyMem_Free(ctx);
}
static PyObject *new_context(PyObject *self, PyObject *args) {
    (void)self;
    PyObject *frozen_dict=NULL,*frozen_list=NULL;
    if (!PyArg_ParseTuple(args,"|OO:new_context",&frozen_dict,&frozen_list)) return NULL;
    if ((frozen_dict && (!PyType_Check(frozen_dict) ||
         !PyType_IsSubtype((PyTypeObject*)frozen_dict,&PyDict_Type))) ||
        (frozen_list && (!PyType_Check(frozen_list) ||
         !PyType_IsSubtype((PyTypeObject*)frozen_list,&PyList_Type)))) {
        PyErr_SetString(PyExc_TypeError,"SNAPSHOT_REVIEWED_CONTAINER_TYPES_REQUIRED"); return NULL;
    }
    Context *ctx = PyMem_Calloc(1, sizeof(Context));
    if (!ctx) return PyErr_NoMemory();
    ctx->enc = PyDict_New(); ctx->dec = PyList_New(0);
    ctx->frozen_dict=Py_XNewRef(frozen_dict); ctx->frozen_list=Py_XNewRef(frozen_list);
    ctx->buffer = PyMem_Malloc(sizeof(Buffer));
    if (!ctx->enc || !ctx->dec || !ctx->buffer) {
        Py_XDECREF(ctx->enc); Py_XDECREF(ctx->dec); Py_XDECREF(ctx->frozen_dict);
        Py_XDECREF(ctx->frozen_list); PyMem_Free(ctx->buffer); PyMem_Free(ctx);
        if (!PyErr_Occurred()) PyErr_NoMemory();
        return NULL;
    }
    PyObject *result = PyCapsule_New(ctx, context_name, context_free);
    if (!result) { Py_DECREF(ctx->enc); Py_DECREF(ctx->dec); Py_XDECREF(ctx->frozen_dict);
        Py_XDECREF(ctx->frozen_list); PyMem_Free(ctx->buffer); PyMem_Free(ctx); }
    return result;
}
static Context *context_get(PyObject *capsule) {
    Context *ctx = PyCapsule_GetPointer(capsule, context_name);
    if (ctx && ctx->bad) {
        PyErr_SetString(PyExc_ValueError, "SNAPSHOT_CONTEXT_POISONED"); return NULL;
    }
    return ctx;
}
static int error(const char *message);
static int dictionary(Context *ctx, PyObject *v) {
    return PyDict_CheckExact(v) || (PyObject*)Py_TYPE(v)==ctx->frozen_dict;
}
static int array(Context *ctx, PyObject *v) {
    return PyList_CheckExact(v) || PyTuple_CheckExact(v) || (PyObject*)Py_TYPE(v)==ctx->frozen_list;
}
static int bound_actual(Context *ctx, PyObject *v, Py_ssize_t *bytes, Py_ssize_t *nodes) {
    if (*nodes<=0) return 0;
    --*nodes;
    if (PyUnicode_CheckExact(v)) {
        Py_ssize_t n; int fit=ascii_bound(v,*bytes,&n);
        if (fit==1) *bytes-=n;
        return fit;
    }
    if (v==Py_None || PyBool_Check(v)) {
        if (*bytes<5) return 0;
        *bytes-=5; return 1;
    }
    if (PyLong_CheckExact(v)) {
        size_t bits=_PyLong_NumBits(v);
        if (bits==(size_t)-1 && PyErr_Occurred()) return -1;
        if (bits<1) bits=1;
        if (*bytes<3 || bits>(size_t)(*bytes-3)) return 0;
        *bytes-=(Py_ssize_t)bits+3; return 1;
    }
    if (PyFloat_CheckExact(v)) {
        if (!isfinite(PyFloat_AS_DOUBLE(v))) return error("SNAPSHOT_NONFINITE");
        if (*bytes<64) return 0;
        *bytes-=64; return 1;
    }
    if (!dictionary(ctx,v) && !array(ctx,v)) return 0;
    if (Py_EnterRecursiveCall(" in actual snapshot value bounds")) return -1;
    int fit=1;
    Py_ssize_t n=dictionary(ctx,v)?PyDict_Size(v):PySequence_Fast_GET_SIZE(v);
    Py_ssize_t overhead=2+n*(dictionary(ctx,v)?2:1);
    if (n>256 || overhead>*bytes) fit=0;
    else {
        *bytes-=overhead;
        if (dictionary(ctx,v)) {
            Py_ssize_t pos=0; PyObject *key,*child;
            while (PyDict_Next(v,&pos,&key,&child)) {
                if (!PyUnicode_CheckExact(key)) {fit=0;break;}
                Py_ssize_t size; fit=ascii_bound(key,*bytes,&size);
                if (fit!=1) break;
                *bytes-=size;
                fit=bound_actual(ctx,child,bytes,nodes);
                if (fit!=1) break;
            }
        } else for (Py_ssize_t i=0;i<n;i++) {
            fit=bound_actual(ctx,PySequence_Fast_GET_ITEM(v,i),bytes,nodes);
            if (fit!=1) break;
        }
    }
    Py_LeaveRecursiveCall(); return fit;
}
static int small(Context *ctx, PyObject *v) {
    Py_ssize_t bytes=65536,nodes=4096;
    return bound_actual(ctx,v,&bytes,&nodes);
}
static int append(Buffer *buffer, const void *data, Py_ssize_t n) {
    Py_ssize_t old=buffer->used;
    if (n<0 || n>PIECE_MAX-old) {
        PyErr_SetString(PyExc_ValueError,"SNAPSHOT_ENCODE_BOUND"); return -1;
    }
    /* Volatile byte stores prohibit a compiler-created libc memcpy import.
       The release module retains its CPython-only symbol dependency rule. */
    volatile unsigned char *target=(volatile unsigned char*)buffer->data+old;
    const unsigned char *source=(const unsigned char*)data;
    for (Py_ssize_t i=0;i<n;i++) target[i]=source[i];
    buffer->used=old+n;
    return 0;
}
static int tag(Buffer *b, char t) { return append(b,&t,1); }
static int u32(Buffer *b, uint32_t n) {
    unsigned char raw[4]={(unsigned char)(n>>24),(unsigned char)(n>>16),
                          (unsigned char)(n>>8),(unsigned char)n};
    return append(b,raw,4);
}
static int encode_value(Context *ctx, Buffer *buffer, PyObject *value) {
    if (Py_EnterRecursiveCall(" in snapshot primitive capture")) return -1;
    int result=-1;
    if (value==Py_None) result=tag(buffer,'N');
    else if (value==Py_True) result=tag(buffer,'T');
    else if (value==Py_False) result=tag(buffer,'F');
    else if (PyUnicode_CheckExact(value)) {
        PyObject *index=PyDict_GetItemWithError(ctx->enc,value);
        if (index) {
            Py_ssize_t n=PyLong_AsSsize_t(index);
            if (n>=0 && n<256) {
                unsigned char raw=(unsigned char)n;
                if (tag(buffer,'r')==0) result=append(buffer,&raw,1);
            } else if (n>=256 && n<MEMO_MAX) {
                unsigned char raw[2]={(unsigned char)(n>>8),(unsigned char)n};
                if (tag(buffer,'R')==0) result=append(buffer,raw,2);
            }
        } else if (!PyErr_Occurred()) {
            PyObject *raw=PyUnicode_AsEncodedString(value,"utf-8","surrogatepass");
            if (raw) {
                Py_ssize_t n=PyBytes_GET_SIZE(raw), count=PyDict_Size(ctx->enc);
                int memo=count<MEMO_MAX && n<=MEMO_BYTES-ctx->enc_bytes;
                if (tag(buffer,memo?'S':'U')==0 && u32(buffer,(uint32_t)n)==0 &&
                    append(buffer,PyBytes_AS_STRING(raw),n)==0) {
                    result=0;
                    if (memo) {
                        PyObject *number=PyLong_FromSsize_t(count);
                        if (!number || PyDict_SetItem(ctx->enc,value,number)<0) result=-1;
                        else ctx->enc_bytes+=n;
                        Py_XDECREF(number);
                    }
                }
                Py_DECREF(raw);
            }
        }
    } else if (PyLong_CheckExact(value)) {
        PyObject *text=PyObject_Str(value);
        PyObject *raw=text?PyUnicode_AsASCIIString(text):NULL; Py_XDECREF(text);
        if (raw) {
            Py_ssize_t n=PyBytes_GET_SIZE(raw);
            if (tag(buffer,'I')==0 && u32(buffer,(uint32_t)n)==0)
                result=append(buffer,PyBytes_AS_STRING(raw),n);
            Py_DECREF(raw);
        }
    } else if (PyFloat_CheckExact(value)) {
        double number=PyFloat_AS_DOUBLE(value); uint64_t bits;
        unsigned char raw[8]; memcpy(&bits,&number,8);
        for (int i=0;i<8;i++) raw[i]=(unsigned char)(bits>>(56-8*i));
        if (tag(buffer,'D')==0) result=append(buffer,raw,8);
    } else if (dictionary(ctx,value)) {
        Py_ssize_t n=PyDict_Size(value),pos=0; PyObject *key,*child;
        if (tag(buffer,'O')==0 && u32(buffer,(uint32_t)n)==0) {
            result=0;
            while (PyDict_Next(value,&pos,&key,&child)) {
                if (encode_value(ctx,buffer,key)<0 || encode_value(ctx,buffer,child)<0) {
                    result=-1; break;
                }
            }
        }
    } else if (array(ctx,value)) {
        Py_ssize_t n=PySequence_Fast_GET_SIZE(value);
        if (tag(buffer,'L')==0 && u32(buffer,(uint32_t)n)==0) {
            result=0;
            for (Py_ssize_t i=0;i<n;i++) {
                if (encode_value(ctx,buffer,PySequence_Fast_GET_ITEM(value,i))<0) {
                    result=-1; break;
                }
            }
        }
    } else PyErr_SetString(PyExc_ValueError,"SNAPSHOT_UNSUPPORTED_AFTER_PREFLIGHT");
    Py_LeaveRecursiveCall(); return result;
}
static PyObject *capture(PyObject *self, PyObject *args) {
    (void)self; PyObject *value,*capsule;
    if (!PyArg_ParseTuple(args,"OO:encode",&value,&capsule)) return NULL;
    Context *ctx=context_get(capsule); if (!ctx) return NULL;
    int fit=small(ctx,value); if (fit<0) return NULL;
    if (!fit) Py_RETURN_NONE;
    Buffer *buffer=ctx->buffer;
    buffer->used=0;
    PyObject *result=NULL;
    if (encode_value(ctx,buffer,value)==0)
        result=PyBytes_FromStringAndSize(buffer->data,buffer->used);
    if (!result) ctx->bad=1;
    return result;
}
typedef struct { const unsigned char *at,*end; Context *ctx; Py_ssize_t nodes; } Reader;
static int error(const char *message) { PyErr_SetString(PyExc_ValueError,message); return -1; }
static int need(Reader *r, Py_ssize_t n) {
    if (n<0 || n>r->end-r->at) return error("SNAPSHOT_TRUNCATED");
    return 0;
}
static int read32(Reader *r, uint32_t *n) {
    if (need(r,4)<0) return -1;
    *n=((uint32_t)r->at[0]<<24)|((uint32_t)r->at[1]<<16)|
       ((uint32_t)r->at[2]<<8)|r->at[3]; r->at+=4; return 0;
}
static PyObject *decode_value(Reader *r) {
    if (--r->nodes<0) {error("SNAPSHOT_NODE_BUDGET"); return NULL;}
    if (need(r,1)<0 || Py_EnterRecursiveCall(" in snapshot primitive decode")) return NULL;
    char t=(char)*r->at++; PyObject *result=NULL; uint32_t n;
    if (t=='N') result=Py_NewRef(Py_None);
    else if (t=='T') result=Py_NewRef(Py_True);
    else if (t=='F') result=Py_NewRef(Py_False);
    else if (t=='R' || t=='r') {
        if (need(r,t=='r'?1:2)==0) {
            if (t=='r') n=*r->at++;
            else { n=((uint32_t)r->at[0]<<8)|r->at[1]; r->at+=2; }
            if (n>=(uint32_t)PyList_GET_SIZE(r->ctx->dec)) error("SNAPSHOT_MEMO_INDEX");
            else result=Py_NewRef(PyList_GET_ITEM(r->ctx->dec,n));
        }
    } else if (t=='S' || t=='U') {
        if (read32(r,&n)==0 && n<=PIECE_MAX && need(r,(Py_ssize_t)n)==0) {
            if (t=='S' && (PyList_GET_SIZE(r->ctx->dec)>=MEMO_MAX ||
                          n>(uint32_t)(MEMO_BYTES-r->ctx->dec_bytes))) error("SNAPSHOT_MEMO_BUDGET");
            else {
                result=PyUnicode_DecodeUTF8((const char*)r->at,n,"surrogatepass"); r->at+=n;
                if (result && t=='S') {
                    if (PyList_Append(r->ctx->dec,result)<0) Py_CLEAR(result);
                    else r->ctx->dec_bytes+=n;
                }
            }
        } else if (!PyErr_Occurred()) error("SNAPSHOT_STRING_BOUND");
    } else if (t=='I') {
        if (read32(r,&n)==0 && n>0 && n<=65536 && need(r,n)==0) {
            uint32_t first=(r->at[0]=='-'); int valid=first<n;
            if (valid && r->at[first]=='0' && (n-first>1 || first)) valid=0;
            for (uint32_t i=first;i<n && valid;i++)
                if (r->at[i]<'0' || r->at[i]>'9') valid=0;
            if (!valid) error("SNAPSHOT_INTEGER_SYNTAX");
            else {
                PyObject *text=PyBytes_FromStringAndSize((const char*)r->at,n);
                if (text) { result=PyLong_FromString(PyBytes_AS_STRING(text),NULL,10); Py_DECREF(text); }
                r->at+=n;
            }
        } else if (!PyErr_Occurred()) error("SNAPSHOT_INTEGER_BOUND");
    } else if (t=='D') {
        if (need(r,8)==0) {
            uint64_t bits=0; double number;
            for (int i=0;i<8;i++) bits=(bits<<8)|r->at[i];
            r->at+=8;
            memcpy(&number,&bits,8);
            if (!isfinite(number)) error("SNAPSHOT_NONFINITE");
            else result=PyFloat_FromDouble(number);
        }
    } else if (t=='L' || t=='O') {
        if (read32(r,&n)==0 && n<=256 && n<=(uint32_t)r->nodes/(t=='O'?2:1)) {
            result=(t=='L')?PyList_New(n):PyDict_New();
            if (result) for (uint32_t i=0;i<n;i++) {
                PyObject *key=NULL,*value=NULL;
                if (t=='O') {
                    key=decode_value(r);
                    if (!key) {Py_CLEAR(result); break;}
                    if (!PyUnicode_CheckExact(key)) {
                        Py_DECREF(key); error("SNAPSHOT_KEY_TYPE"); Py_CLEAR(result); break;
                    }
                    int present=PyDict_Contains(result,key);
                    if (present) {
                        Py_DECREF(key); if (present>0) error("SNAPSHOT_DUPLICATE_KEY");
                        Py_CLEAR(result); break;
                    }
                }
                value=decode_value(r);
                if (!value) {Py_XDECREF(key); Py_CLEAR(result); break;}
                if (t=='L') PyList_SET_ITEM(result,i,value);
                else {
                    int inserted=PyDict_SetItem(result,key,value);
                    Py_DECREF(key); Py_DECREF(value);
                    if (inserted<0) {Py_CLEAR(result); break;}
                }
            }
        } else if (!PyErr_Occurred()) error("SNAPSHOT_CONTAINER_BOUND");
    } else error("SNAPSHOT_TAG");
    Py_LeaveRecursiveCall(); return result;
}
static PyObject *restore(PyObject *self, PyObject *args) {
    (void)self; PyObject *raw,*capsule;
    if (!PyArg_ParseTuple(args,"OO:decode",&raw,&capsule)) return NULL;
    Context *ctx=context_get(capsule); if (!ctx) return NULL;
    if (!PyBytes_CheckExact(raw) || PyBytes_GET_SIZE(raw)<1 || PyBytes_GET_SIZE(raw)>PIECE_MAX) {
        error("SNAPSHOT_PIECE_BOUND"); ctx->bad=1; return NULL;
    }
    Reader r={(const unsigned char*)PyBytes_AS_STRING(raw),
              (const unsigned char*)PyBytes_AS_STRING(raw)+PyBytes_GET_SIZE(raw),ctx,8192};
    PyObject *value=decode_value(&r);
    if (value && r.at!=r.end) {error("SNAPSHOT_TRAILING_BYTES"); Py_CLEAR(value);}
    if (value) {
        int fit=small(ctx,value);
        if (fit!=1) {if (fit==0) error("SNAPSHOT_DECODED_JSON_BOUND"); Py_CLEAR(value);}
    }
    if (!value) ctx->bad=1;
    return value;
}
static PyMethodDef methods[]={
    {"is_small",is_small,METH_VARARGS,"Bound canonical ASCII JSON tree size without encoding."},
    {"snapshot_context",new_context,METH_VARARGS,"New bounded role-local actual-value context."},
    {"snapshot_encode",capture,METH_VARARGS,"Capture bounded actual JSON primitives, or None."},
    {"snapshot_decode",restore,METH_VARARGS,"Decode fixed non-executable bounded primitives."},
    {NULL,NULL,0,NULL}
};
static struct PyModuleDef module={PyModuleDef_HEAD_INIT,"_rc6_json_bound",NULL,-1,methods,NULL,NULL,NULL,NULL};
PyMODINIT_FUNC PyInit__rc6_json_bound(void) {
    _Static_assert(sizeof(double)==8,"IEEE64 double required");
    return PyModule_Create(&module);
}
